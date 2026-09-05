"""Retry delay.

docs/07-EXECUTION-ENGINE.md section 6: base 1s, factor 2, cap 30s, plus jitter.

Full jitter (``delay ~ Uniform(0, capped_exp)``) is used rather than
``exp ± spread``: it prevents synchronized retry storms when a whole DAG of
actions fails together, which is the actual failure mode of a personal agent
hitting a rate-limited API, not a single action.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Backoff:
    base_s: float = 1.0
    factor: float = 2.0
    cap_s: float = 30.0


DEFAULT_BACKOFF = Backoff()


def delay_s(
    attempt: int,
    *,
    backoff: Backoff = DEFAULT_BACKOFF,
    jitter: bool = True,
    rng: random.Random | None = None,
) -> float:
    """Seconds to wait after a failed attempt before the action is READY again.

    ``attempt`` is 1-based and already incremented by the worker claim
    (the attempt that just failed). ``attempt=1`` → ``base_s`` before jitter.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    capped = min(backoff.cap_s, backoff.base_s * (backoff.factor ** (attempt - 1)))
    if not jitter:
        return capped
    picker = rng.random if rng is not None else random.random
    return picker() * capped
