r"""Filesystem sandbox (FR-603).

The sandbox is the last line between a confused plan and the rest of the disk,
so the tests are written adversarially: every case below is a way a path that
*looks* contained turns out not to be. Traversal is checked after resolution,
because ``root/../../etc`` is a legal string and an illegal destination.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from astra.core.errors import ErrorCode, ToolError
from astra.tools.sandbox import resolve_in_sandbox


@pytest.mark.req("FR-603")
def test_a_path_inside_a_root_resolves(tmp_path: Path) -> None:
    target = tmp_path / "notes" / "today.md"
    target.parent.mkdir()
    target.write_text("hi", encoding="utf-8")

    assert resolve_in_sandbox(str(target), [tmp_path]) == target.resolve()
    # A root is inside itself.
    assert resolve_in_sandbox(str(tmp_path), [tmp_path]) == tmp_path.resolve()


@pytest.mark.req("FR-603")
def test_an_empty_allowlist_denies_everything(tmp_path: Path) -> None:
    """Fail closed. An unconfigured agent reads nothing rather than everything."""
    with pytest.raises(ToolError) as caught:
        resolve_in_sandbox(str(tmp_path), [])
    assert caught.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.req("FR-603")
@pytest.mark.parametrize(
    "escape",
    [
        "../outside.txt",
        "../../outside.txt",
        "sub/../../outside.txt",
        "./sub/./../../outside.txt",
    ],
)
def test_traversal_out_of_a_root_is_rejected(tmp_path: Path, escape: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")

    with pytest.raises(ToolError) as caught:
        resolve_in_sandbox(str(root / escape), [root])
    assert caught.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.req("FR-603")
def test_a_sibling_directory_with_a_shared_prefix_is_not_inside(tmp_path: Path) -> None:
    """``/data/project-secrets`` must not pass because ``/data/project`` is
    allowed. String prefix checks get this wrong; path containment does not."""
    allowed = tmp_path / "project"
    allowed.mkdir()
    sibling = tmp_path / "project-secrets"
    sibling.mkdir()

    with pytest.raises(ToolError):
        resolve_in_sandbox(str(sibling / "keys.txt"), [allowed])


@pytest.mark.req("FR-603")
def test_an_absolute_path_elsewhere_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ToolError):
        resolve_in_sandbox("C:/Windows/System32/config/SAM", [tmp_path])


@pytest.mark.req("FR-603")
@pytest.mark.parametrize("bad", ["", "with\x00null"])
def test_structurally_invalid_paths_are_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ToolError) as caught:
        resolve_in_sandbox(bad, [tmp_path])
    assert caught.value.code is ErrorCode.INVALID_PARAMETERS


@pytest.mark.req("FR-603")
def test_the_first_matching_root_wins_and_others_are_irrelevant(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    unresolvable = Path("Z:/definitely/not/mounted")

    # An unusable root must not abort the search for a usable one.
    assert resolve_in_sandbox(str(b / "f.txt"), [unresolvable, a, b]) == (b / "f.txt").resolve()


@pytest.mark.req("FR-603")
@given(
    segments=st.lists(
        st.sampled_from(["..", ".", "sub", "deep", "a b", "x"]), min_size=1, max_size=6
    )
)
def test_no_sequence_of_segments_ever_escapes(segments: list[str]) -> None:
    """Property: whatever comes back is inside a root, or nothing comes back.

    Uses a fixed root rather than ``tmp_path`` because Hypothesis re-runs the
    body many times and function-scoped fixtures would not be reset between
    examples.
    """
    root = Path(__file__).resolve().parent
    candidate = str(root.joinpath(*segments))
    try:
        resolved = resolve_in_sandbox(candidate, [root])
    except ToolError:
        return
    assert resolved == root or resolved.is_relative_to(root)
