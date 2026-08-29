"""Retry backoff (FR-206)."""

from __future__ import annotations

import random

import pytest

from astra.runtime.retry import Backoff, delay_s


@pytest.mark.req("FR-206")
def test_exponential_growth_without_jitter() -> None:
    backoff = Backoff(base_s=1.0, factor=2.0, cap_s=30.0)
    assert delay_s(1, backoff=backoff, jitter=False) == 1.0
    assert delay_s(2, backoff=backoff, jitter=False) == 2.0
    assert delay_s(3, backoff=backoff, jitter=False) == 4.0
    assert delay_s(10, backoff=backoff, jitter=False) == 30.0


@pytest.mark.req("FR-206")
def test_jitter_is_bounded_by_the_capped_exponential() -> None:
    rng = random.Random(0)
    backoff = Backoff(base_s=1.0, factor=2.0, cap_s=30.0)
    samples = [delay_s(4, backoff=backoff, jitter=True, rng=rng) for _ in range(200)]
    # attempt 4 → 8s before jitter. Full jitter ⇒ [0, 8].
    assert min(samples) >= 0.0
    assert max(samples) <= 8.0
    assert len({round(s, 5) for s in samples}) > 50  # not a constant


@pytest.mark.req("FR-206")
def test_attempt_is_one_based() -> None:
    with pytest.raises(ValueError):
        delay_s(0, jitter=False)
