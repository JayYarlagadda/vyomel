"""Content-addressed blob store for large action results."""

from __future__ import annotations

import pytest

from astra.store.blobs import (
    BLOB_REF_KEY,
    BlobError,
    is_blob_ref,
    read_blob,
    resolve_result,
    spill_if_large,
    write_blob,
)


def test_small_result_is_not_spilled(tmp_path) -> None:
    result = {"summary": "short"}
    assert spill_if_large(result, blob_dir=tmp_path, threshold=1024) == result
    assert list(tmp_path.iterdir()) == []


def test_large_result_spills_to_content_addressed_blob(tmp_path) -> None:
    result = {"payload": "x" * 10_000}
    stored = spill_if_large(result, blob_dir=tmp_path, threshold=512)
    assert is_blob_ref(stored)
    digest = stored[BLOB_REF_KEY]
    assert (tmp_path / digest).is_file()
    assert resolve_result(stored, blob_dir=tmp_path) == result


def test_write_blob_is_idempotent(tmp_path) -> None:
    data = b"same-bytes"
    first = write_blob(data, tmp_path)
    second = write_blob(data, tmp_path)
    assert first == second
    assert len(list(tmp_path.iterdir())) == 1


def test_resolve_result_passes_through_inline_dict(tmp_path) -> None:
    inline = {"ok": True}
    assert resolve_result(inline, blob_dir=tmp_path) == inline
    assert resolve_result(None, blob_dir=tmp_path) is None


def test_read_blob_raises_when_missing(tmp_path) -> None:
    with pytest.raises(BlobError):
        read_blob("missing", tmp_path)
