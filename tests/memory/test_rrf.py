from __future__ import annotations

from vyomel.memory.rrf import rrf


def test_rrf_prefers_items_that_rank_in_both_lists() -> None:
    fused = rrf(["a", "b", "c"], ["b", "a", "d"], k=60)
    ids = [item_id for item_id, _ in fused]
    assert ids[0] in {"a", "b"}
    assert ids.index("a") < ids.index("c")
    assert ids.index("b") < ids.index("d")


def test_rrf_is_stable_on_tied_scores() -> None:
    fused = rrf(["x"], ["y"])
    assert [item_id for item_id, _ in fused] == ["x", "y"]
