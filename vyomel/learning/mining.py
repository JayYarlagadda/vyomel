"""PrefixSpan-style frequent sequence mining over action signatures (FR-901)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from vyomel.learning.signatures import ActionSignature, ObservedAction, normalize_observed


@dataclass(frozen=True, slots=True)
class FrequentSequence:
    signatures: tuple[ActionSignature, ...]
    support: int
    task_ids: tuple[str, ...]

    @property
    def length(self) -> int:
        return len(self.signatures)

    def pattern_key(self) -> str:
        return " > ".join(sig.key() for sig in self.signatures)


def sequences_from_actions(
    actions: list[ObservedAction],
) -> list[tuple[str, list[ActionSignature]]]:
    """Group observed actions by task_id, preserving encounter order."""
    by_task: dict[str, list[ActionSignature]] = defaultdict(list)
    order: list[str] = []
    for action in actions:
        tid = action.task_id or "_anon"
        if tid not in by_task:
            order.append(tid)
        by_task[tid].append(normalize_observed(action))
    return [(tid, by_task[tid]) for tid in order]


def mine_frequent_sequences(
    sequences: list[tuple[str, list[ActionSignature]]],
    *,
    min_support: int = 3,
    min_length: int = 3,
    max_length: int = 12,
) -> list[FrequentSequence]:
    """Mine contiguous frequent subsequences (PrefixSpan-lite).

    Contiguous patterns match how Vyomel tasks actually execute tools — a
    recurring *pipeline*, not arbitrary sparse subsequences.
    """
    if min_support < 1 or min_length < 1:
        raise ValueError("min_support and min_length must be >= 1")

    # Map pattern key → (signatures, supporting task ids)
    support_map: dict[str, tuple[tuple[ActionSignature, ...], set[str]]] = {}

    for task_id, seq in sequences:
        if len(seq) < min_length:
            continue
        seen_in_task: set[str] = set()
        upper = min(len(seq), max_length)
        for length in range(min_length, upper + 1):
            for start in range(0, len(seq) - length + 1):
                window = tuple(seq[start : start + length])
                key = " > ".join(s.key() for s in window)
                if key in seen_in_task:
                    continue
                seen_in_task.add(key)
                if key not in support_map:
                    support_map[key] = (window, set())
                support_map[key][1].add(task_id)

    results: list[FrequentSequence] = []
    for _key, (sigs, tasks) in support_map.items():
        if len(tasks) >= min_support:
            results.append(
                FrequentSequence(
                    signatures=sigs,
                    support=len(tasks),
                    task_ids=tuple(sorted(tasks)),
                )
            )

    results.sort(key=lambda item: (-item.support, -item.length, item.pattern_key()))
    # Prefer longer patterns when they share the same support: drop strict
    # sub-patterns that never appear outside a longer match.
    return _drop_dominated(results)


def _drop_dominated(items: list[FrequentSequence]) -> list[FrequentSequence]:
    kept: list[FrequentSequence] = []
    for candidate in items:
        dominated = False
        cand_key = candidate.pattern_key()
        for other in items:
            if other is candidate:
                continue
            if (
                other.support >= candidate.support
                and other.length > candidate.length
                and cand_key in other.pattern_key()
            ):
                dominated = True
                break
        if not dominated:
            kept.append(candidate)
    return kept


def pattern_histogram(sequences: list[tuple[str, list[ActionSignature]]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for _tid, seq in sequences:
        counts.update(sig.key() for sig in seq)
    return counts
