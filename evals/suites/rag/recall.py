"""RAG recall@k harness for the committed synthetic corpus (docs/11-EVALUATION.md)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from vyomel.core.config import Settings
from vyomel.memory.ingest import ingest_paths
from vyomel.memory.retrieve import Strategy, retrieve
from vyomel.models.embeddings import get_embedder
from vyomel.store.db import dispose_engine, init_engine, session_scope

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "corpus"
QUESTIONS = Path(__file__).resolve().parents[2] / "fixtures" / "rag" / "questions.jsonl"


@dataclass(frozen=True, slots=True)
class Question:
    query: str
    gold_paths: tuple[str, ...]
    category: str


def load_questions(path: Path) -> list[Question]:
    items: list[Question] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        items.append(
            Question(
                query=row["query"],
                gold_paths=tuple(row["gold_paths"]),
                category=row.get("category", "unknown"),
            )
        )
    return items


def recall_at_k(hits: list[str], gold: tuple[str, ...], k: int) -> float:
    top = [hit.replace("\\", "/") for hit in hits[:k]]
    return 1.0 if any(any(hit.endswith(gold_path) for gold_path in gold) for hit in top) else 0.0


async def run_eval(settings: Settings, *, k: int = 10, strategy: Strategy = "hybrid") -> float:
    embedder = get_embedder(settings)
    init_engine(settings)
    try:
        paths = [str(path) for path in sorted(CORPUS.rglob("*")) if path.is_file()]
        async with session_scope() as session:
            await ingest_paths(
                session, paths, settings.allowed_roots, recursive=True, embedder=embedder
            )

        questions = load_questions(QUESTIONS)
        scores: list[float] = []
        async with session_scope() as session:
            for question in questions:
                retrieval = await retrieve(
                    session,
                    question.query,
                    k=k,
                    strategy=strategy,
                    embedder=embedder,
                )
                hit_paths = [hit.citation.path for hit in retrieval.results]
                scores.append(recall_at_k(hit_paths, question.gold_paths, k))
        return sum(scores) / len(scores) if scores else 0.0
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute recall@k on the synthetic RAG corpus.")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument(
        "--backend",
        choices=("auto", "hashing", "bge"),
        default="hashing",
        help="hashing is fast plumbing; bge is the M4 quality number.",
    )
    parser.add_argument(
        "--strategy",
        choices=("hybrid", "vector", "lexical"),
        default="hybrid",
    )
    args = parser.parse_args()
    corpus_parent = CORPUS.parent
    settings = Settings(
        env="dev",
        embedding_backend=args.backend,
        allowed_roots=[corpus_parent],
        workspace_root=corpus_parent / ".vyomel-eval",
    )
    settings.ensure_directories()

    score = asyncio.run(run_eval(settings, k=args.k, strategy=args.strategy))  # type: ignore[arg-type]
    print(f"recall@{args.k}={score:.3f} backend={args.backend} strategy={args.strategy}")
    if args.backend == "bge" and score < 0.85:
        print("below NFR-04 target (0.85); expected until corpus grows", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
