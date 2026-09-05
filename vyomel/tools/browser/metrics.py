"""Actuation tier accounting for browser tools."""

from __future__ import annotations

from collections import Counter
from threading import Lock

_lock = Lock()
_counts: Counter[int] = Counter()


def record_actuation_tier(tier: int) -> None:
    with _lock:
        _counts[tier] += 1


def actuation_tier_distribution() -> dict[str, int]:
    with _lock:
        return {str(tier): count for tier, count in sorted(_counts.items())}


def reset_actuation_tiers() -> None:
    with _lock:
        _counts.clear()
