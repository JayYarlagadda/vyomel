from __future__ import annotations

import math

from astra.core.config import Settings
from astra.models.embeddings import EMBEDDING_DIM, HashingEmbedder, get_embedder


def test_hashing_embedder_is_384d_unit_and_deterministic() -> None:
    embedder = HashingEmbedder()
    first = embedder.embed(["Orbit gateway failover"])[0]
    second = embedder.embed(["Orbit gateway failover"])[0]
    assert first == second
    assert len(first) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0, rel_tol=1e-6)


def test_get_embedder_uses_hashing_in_test_env() -> None:
    settings = Settings(env="test", embedding_backend="auto")
    embedder = get_embedder(settings)
    assert isinstance(embedder, HashingEmbedder)


def test_get_embedder_honors_hashing_override_in_prod() -> None:
    settings = Settings(env="prod", embedding_backend="hashing")
    embedder = get_embedder(settings)
    assert isinstance(embedder, HashingEmbedder)
