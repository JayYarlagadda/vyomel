"""Embedding providers.

Production uses ``bge-small-en-v1.5`` (ADR-0007). Tests and offline ingest use a
deterministic bag-of-words hasher of the same dimensionality so retrieval can
be proven without downloading weights. The model name is stored on every chunk
so a later re-embed can be incremental.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from astra.core.config import Settings

EMBEDDING_DIM = 384


@runtime_checkable
class Embedder(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Token-hashed bag-of-words into 384 dimensions, L2-normalized.

    Not a substitute for bge on quality. It is a substitute for bge on
    *shape*: the same column, the same cosine query, and lexical identifiers
    still dominate hybrid fusion — which is the property under test until the
    real model is wired.
    """

    @property
    def name(self) -> str:
        return "hashing-bow-384"

    @property
    def dimensions(self) -> int:
        return EMBEDDING_DIM

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [_vectorize(text) for text in texts]


class BgeEmbedder:
    """Local ``sentence-transformers`` backend for ``bge-small-en-v1.5``."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "bge embeddings require the memory extra: pip install -e '.[memory]'"
            ) from exc
        self._model_name = model_name
        self._model = SentenceTransformer(model_name)

    @property
    def name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return EMBEDDING_DIM

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]


def get_embedder(settings: Settings) -> Embedder:
    """Resolve the embedder for this process.

    CI and unit tests stay on the hashing stand-in. Production defaults to bge
    when ``embedding_backend`` is ``auto``.
    """
    backend = settings.embedding_backend
    if backend == "hashing" or (backend == "auto" and settings.env == "test"):
        return HashingEmbedder()
    return BgeEmbedder(model_name=settings.embedding_model)


def _vectorize(text: str) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    for token in text.casefold().split():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % EMBEDDING_DIM
        vec[index] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]
