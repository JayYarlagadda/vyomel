"""Reciprocal Rank Fusion (docs/08-MEMORY-RAG.md §4.1).

``score(d) = Σ_r 1 / (k + rank_r(d))`` with k=60. RRF needs no score
normalization, which is why it is used instead of weighted fusion of cosine
and ts_rank — those scales are not comparable and would need per-corpus tuning.
"""

from __future__ import annotations

from collections.abc import Sequence


def rrf(*rankings: Sequence[str], k: int = 60) -> list[tuple[str, float]]:
    """Fuse ranked id lists. Rank is 1-based. Ties break on id for stability."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
