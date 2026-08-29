from __future__ import annotations

import math

from astra.models.embeddings import EMBEDDING_DIM, HashingEmbedder


def test_hashing_embedder_is_384d_unit_and_deterministic() -> None:
    embedder = HashingEmbedder()
    first = embedder.embed(["Orbit gateway failover"])[0]
    second = embedder.embed(["Orbit gateway failover"])[0]
    assert first == second
    assert len(first) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0, rel_tol=1e-6)
