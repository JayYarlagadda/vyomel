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
from typing import Protocol, runtime_checkable

EMBEDDING_DIM = 384


@runtime_checkable
class Embedder(Protocol):
    name: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Token-hashed bag-of-words into 384 dimensions, L2-normalized.

    Not a substitute for bge on quality. It is a substitute for bge on
    *shape*: the same column, the same cosine query, and lexical identifiers
    still dominate hybrid fusion — which is the property under test until the
    real model is wired.
    """

    name = "hashing-bow-384"
    dimensions = EMBEDDING_DIM

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [_vectorize(text) for text in texts]


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
