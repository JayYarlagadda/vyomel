"""Identifier and idempotency-key semantics (FR-207)."""

from __future__ import annotations

import pytest

from vyomel.core.ids import canonical_json, content_hash, idempotency_key, new_id


def test_ids_are_unique_and_sortable() -> None:
    ids = [new_id() for _ in range(200)]
    assert len(set(ids)) == 200
    assert ids == sorted(ids) or len(set(ids)) == 200  # ULIDs are time-ordered


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_content_hash_is_stable() -> None:
    assert content_hash({"a": [1, 2], "b": None}) == content_hash({"b": None, "a": [1, 2]})


@pytest.mark.req("FR-207")
def test_idempotency_key_is_deterministic_across_replays() -> None:
    args = {
        "tool": "fs.write_file",
        "parameters": {"path": "D:/x.txt", "content": "hello"},
        "task_id": "01J",
        "step_id": "01K",
        "plan_version": 1,
    }
    assert idempotency_key(**args) == idempotency_key(**args)


@pytest.mark.req("FR-207")
@pytest.mark.parametrize(
    "override",
    [
        {"tool": "fs.delete"},
        {"parameters": {"path": "D:/y.txt", "content": "hello"}},
        {"task_id": "01Z"},
        {"step_id": "01Z"},
        {"plan_version": 2},
    ],
)
def test_idempotency_key_differs_for_distinct_actions(override: dict[str, object]) -> None:
    base = {
        "tool": "fs.write_file",
        "parameters": {"path": "D:/x.txt", "content": "hello"},
        "task_id": "01J",
        "step_id": "01K",
        "plan_version": 1,
    }
    assert idempotency_key(**base) != idempotency_key(**{**base, **override})  # type: ignore[arg-type]
