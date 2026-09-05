"""Ingest local markdown and text into chunked, embedded rows (FR-501, FR-506).

pdf/docx/html/code wait for later extractors. Embeddings are computed here and
stored only on chunks (FR-504). Unchanged ``content_hash`` is a no-op.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from vyomel.core.errors import ErrorCode, NotFoundError, VyomelError
from vyomel.core.ids import file_digest, new_id
from vyomel.memory.chunking import chunk_text
from vyomel.memory.extract import extract_text
from vyomel.memory.graph import upsert_document_entity
from vyomel.memory.paths import is_ingestible, mime_for, resolve_allowed
from vyomel.models.embeddings import Embedder, HashingEmbedder
from vyomel.store.models import Document, DocumentChunk

IngestStatus = Literal["ingested", "skipped", "replaced"]


@dataclass(frozen=True, slots=True)
class FileIngest:
    path: str
    status: IngestStatus
    document_id: str
    chunk_count: int
    version: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class IngestReport:
    documents: tuple[FileIngest, ...]

    @property
    def ingested(self) -> int:
        return sum(1 for item in self.documents if item.status != "skipped")


async def ingest_paths(
    session: AsyncSession,
    paths: Sequence[str],
    allowed_roots: Sequence[Path],
    *,
    recursive: bool = False,
    embedder: Embedder | None = None,
) -> IngestReport:
    encoder = embedder or HashingEmbedder()
    files = _collect(paths, allowed_roots, recursive=recursive)
    results: list[FileIngest] = []
    for file in files:
        results.append(await _ingest_one(session, file, encoder))
    return IngestReport(documents=tuple(results))


def _collect(paths: Sequence[str], allowed_roots: Sequence[Path], *, recursive: bool) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        resolved = resolve_allowed(raw, allowed_roots)
        if resolved.is_dir():
            if not recursive:
                raise VyomelError(
                    f"{resolved} is a directory; pass recursive=true to walk it",
                    code=ErrorCode.INVALID_PARAMETERS,
                )
            for child in sorted(resolved.rglob("*")):
                if child.is_file() and is_ingestible(child) and child not in seen:
                    seen.add(child)
                    found.append(child)
            continue
        if not resolved.is_file():
            raise NotFoundError(f"No file at {resolved}")
        if not is_ingestible(resolved):
            raise VyomelError(
                f"Unsupported ingest type in this slice: {resolved.suffix}",
                code=ErrorCode.INVALID_PARAMETERS,
            )
        if resolved not in seen:
            seen.add(resolved)
            found.append(resolved)
    return found


async def _ingest_one(session: AsyncSession, path: Path, embedder: Embedder) -> FileIngest:
    digest = file_digest(path)
    stored = path.as_posix()
    existing = await session.scalar(
        select(Document).options(selectinload(Document.chunks)).where(Document.path == stored)
    )
    if existing is not None and existing.content_hash == digest:
        await upsert_document_entity(session, document=existing, path=path)
        return FileIngest(
            path=stored,
            status="skipped",
            document_id=existing.id,
            chunk_count=len(existing.chunks),
            version=existing.version,
            content_hash=digest,
        )

    text, size_bytes = extract_text(path)
    chunks = chunk_text(text)
    vectors = embedder.embed([chunk.content for chunk in chunks]) if chunks else []

    if existing is None:
        document = Document(
            id=new_id(),
            path=stored,
            mime=mime_for(path),
            content_hash=digest,
            size_bytes=size_bytes,
            version=1,
        )
        session.add(document)
        status: IngestStatus = "ingested"
    else:
        existing.chunks.clear()
        existing.content_hash = digest
        existing.size_bytes = size_bytes
        existing.mime = mime_for(path)
        existing.version += 1
        document = existing
        status = "replaced"

    await session.flush()
    for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        session.add(
            DocumentChunk(
                id=new_id(),
                document_id=document.id,
                ordinal=ordinal,
                content=chunk.content,
                token_count=chunk.token_count,
                heading_path=list(chunk.heading_path),
                page=None,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                embedding=vector,
                embedding_model=embedder.name,
            )
        )
    await upsert_document_entity(session, document=document, path=path)
    return FileIngest(
        path=stored,
        status=status,
        document_id=document.id,
        chunk_count=len(chunks),
        version=document.version,
        content_hash=digest,
    )
