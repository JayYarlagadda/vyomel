"""Filesystem tools.

Read and list are L0. Mutating tools are reversible: writes back up prior
bytes, move/copy back up a clobbered destination, and delete moves to
``ctx.trash_dir`` instead of unlinking so cancel can restore.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from astra.core.errors import ErrorCode, ToolError
from astra.core.ids import digest_bytes, file_digest
from astra.core.types import Capability
from astra.tools.base import Tool, ToolContext
from astra.tools.sandbox import resolve_in_sandbox

_MAX_WRITE_BYTES = 8_000_000


class ReadFileInput(BaseModel):
    path: str
    max_bytes: int = Field(default=1_000_000, ge=1, le=8_000_000)


class ReadFileOutput(BaseModel):
    path: str
    content: str
    bytes: int


class ListDirInput(BaseModel):
    path: str
    max_entries: int = Field(default=500, ge=1, le=5_000)


class DirEntry(BaseModel):
    name: str
    kind: Literal["file", "dir", "other"]
    size: int | None = None


class ListDirOutput(BaseModel):
    path: str
    entries: list[DirEntry]


class WriteFileInput(BaseModel):
    path: str
    content: str = Field(max_length=_MAX_WRITE_BYTES)


class WriteFileOutput(BaseModel):
    path: str
    bytes: int
    sha256: str
    created: bool
    backup_path: str | None = None


class ReadFile(Tool):
    name: ClassVar[str] = "fs.read_file"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Read a UTF-8 text file inside the allowlisted roots. Fails if the file "
        "is missing, is not a file, exceeds max_bytes, or is not valid UTF-8."
    )
    Input: ClassVar[type[BaseModel]] = ReadFileInput
    Output: ClassVar[type[BaseModel]] = ReadFileOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "fs"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ReadFileInput)
        target = resolve_in_sandbox(params.path, ctx.allowed_roots)
        if not target.exists():
            raise ToolError(
                "File does not exist",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=str(target),
            )
        if not target.is_file():
            raise ToolError(
                "Path is not a file",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=str(target),
            )
        size = target.stat().st_size
        if size > params.max_bytes:
            raise ToolError(
                f"File is {size} bytes, over the {params.max_bytes} byte cap",
                code=ErrorCode.INVALID_PARAMETERS,
                observation=str(target),
            )
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(
                "File is not valid UTF-8",
                code=ErrorCode.INVALID_PARAMETERS,
                observation=str(target),
            ) from exc
        return ReadFileOutput(path=str(target), content=content, bytes=size)


class ListDir(Tool):
    name: ClassVar[str] = "fs.list_dir"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "List entries in a directory inside the allowlisted roots. Does not recurse."
    )
    Input: ClassVar[type[BaseModel]] = ListDirInput
    Output: ClassVar[type[BaseModel]] = ListDirOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "fs"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ListDirInput)
        target = resolve_in_sandbox(params.path, ctx.allowed_roots)
        if not target.exists() or not target.is_dir():
            raise ToolError(
                "Path is not an existing directory",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=str(target),
            )
        entries: list[DirEntry] = []
        # Sort so the same directory always produces the same observation.
        children = sorted(target.iterdir(), key=lambda p: p.name.lower())
        for child in children[: params.max_entries]:
            entries.append(_entry(child))
        return ListDirOutput(path=str(target), entries=entries)


class WriteFile(Tool):
    """Write UTF-8 text inside the allowlisted roots.

    L1 inside a ``scratch`` directory (trivially disposable), L2 elsewhere.
    Overwrites copy the previous bytes to ``ctx.scratch_dir/backups/<action>``
    so cancel can restore them. New files are unlinked on compensate.
    """

    name: ClassVar[str] = "fs.write_file"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Write a UTF-8 text file inside the allowlisted roots. Creates parent "
        "directories. Overwrites copy the previous content to a backup so the "
        "write can be compensated. Capability is L1 inside scratch, L2 elsewhere."
    )
    Input: ClassVar[type[BaseModel]] = WriteFileInput
    Output: ClassVar[type[BaseModel]] = WriteFileOutput
    base_capability: ClassVar[Capability] = Capability.L1
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "fs"

    def classify(self, params: BaseModel) -> Capability:
        assert isinstance(params, WriteFileInput)
        try:
            resolved = Path(params.path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            return Capability.L2
        if "scratch" in resolved.parts:
            return Capability.L1
        return Capability.L2

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(result, WriteFileOutput)
        return [
            {"type": "file_exists", "path": result.path, "tier": 1},
            {
                "type": "file_hash",
                "path": result.path,
                "expected": result.sha256,
                "tier": 1,
            },
        ]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, WriteFileInput)
        payload = params.content.encode("utf-8")
        if len(payload) > _MAX_WRITE_BYTES:
            raise ToolError(
                f"Write is {len(payload)} bytes, over the {_MAX_WRITE_BYTES} byte cap",
                code=ErrorCode.INVALID_PARAMETERS,
            )
        target = resolve_in_sandbox(params.path, ctx.allowed_roots)
        created = not target.exists()
        backup_path: str | None = None
        if not created:
            if not target.is_file():
                raise ToolError(
                    "Path exists and is not a file",
                    code=ErrorCode.PRECONDITION_FAILED,
                    observation=str(target),
                )
            backup = ctx.scratch_dir / "backups" / ctx.action_id
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            backup_path = str(backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return WriteFileOutput(
            path=str(target),
            bytes=len(payload),
            sha256=digest_bytes(payload),
            created=created,
            backup_path=backup_path,
        )

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        assert isinstance(params, WriteFileInput)
        assert isinstance(result, WriteFileOutput)
        target = resolve_in_sandbox(params.path, ctx.allowed_roots)
        if result.created:
            target.unlink(missing_ok=True)
            return
        if result.backup_path is None:
            return
        backup = Path(result.backup_path)
        try:
            shutil.copy2(backup, target)
        except FileNotFoundError:
            return


class MoveInput(BaseModel):
    src: str
    dest: str


class MoveOutput(BaseModel):
    src: str
    dest: str
    sha256: str | None = None
    is_dir: bool
    overwritten: bool
    backup_path: str | None = None


class CopyInput(BaseModel):
    src: str
    dest: str


class CopyOutput(BaseModel):
    src: str
    dest: str
    sha256: str | None = None
    is_dir: bool
    created: bool
    backup_path: str | None = None


class DeleteInput(BaseModel):
    path: str


class DeleteOutput(BaseModel):
    path: str
    trashed_to: str
    is_dir: bool
    sha256: str | None = None


class Move(Tool):
    """Move a file or directory inside the allowlisted roots.

    Overwriting a destination copies it to scratch first so compensate can
    put both sides back. Capability is L2: the change is durable.
    """

    name: ClassVar[str] = "fs.move"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Move a file or directory inside the allowlisted roots. Overwriting a "
        "file copies the previous destination to a backup so the move can be "
        "compensated. Refuses to overwrite a directory."
    )
    Input: ClassVar[type[BaseModel]] = MoveInput
    Output: ClassVar[type[BaseModel]] = MoveOutput
    base_capability: ClassVar[Capability] = Capability.L2
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "fs"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(result, MoveOutput)
        return _dest_postconditions(result.dest, result.sha256, result.is_dir)

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, MoveInput)
        src = resolve_in_sandbox(params.src, ctx.allowed_roots)
        dest = resolve_in_sandbox(params.dest, ctx.allowed_roots)
        if not src.exists():
            if dest.exists():
                # Replay: the move already happened.
                return _move_output(src, dest, overwritten=False, backup_path=None)
            raise ToolError(
                "Source does not exist",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=str(src),
            )
        if dest.exists() and dest.is_dir():
            raise ToolError(
                "Destination exists and is a directory",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=str(dest),
            )
        overwritten = dest.exists()
        backup_path = _backup_existing(dest, ctx) if overwritten else None
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return _move_output(src, dest, overwritten=overwritten, backup_path=backup_path)

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        assert isinstance(params, MoveInput)
        assert isinstance(result, MoveOutput)
        src = resolve_in_sandbox(params.src, ctx.allowed_roots)
        dest = resolve_in_sandbox(params.dest, ctx.allowed_roots)
        if dest.exists() and not src.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(src))
        _restore_backup(result.backup_path, dest)


class Copy(Tool):
    """Copy a file or directory inside the allowlisted roots."""

    name: ClassVar[str] = "fs.copy"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Copy a file or directory inside the allowlisted roots. Overwriting a "
        "file copies the previous destination to a backup so the copy can be "
        "compensated. Refuses to overwrite a directory."
    )
    Input: ClassVar[type[BaseModel]] = CopyInput
    Output: ClassVar[type[BaseModel]] = CopyOutput
    base_capability: ClassVar[Capability] = Capability.L2
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "fs"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(result, CopyOutput)
        return _dest_postconditions(result.dest, result.sha256, result.is_dir)

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, CopyInput)
        src = resolve_in_sandbox(params.src, ctx.allowed_roots)
        dest = resolve_in_sandbox(params.dest, ctx.allowed_roots)
        if not src.exists():
            raise ToolError(
                "Source does not exist",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=str(src),
            )
        if dest.exists() and dest.is_dir():
            raise ToolError(
                "Destination exists and is a directory",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=str(dest),
            )
        created = not dest.exists()
        backup_path = _backup_existing(dest, ctx) if not created else None
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dest.exists():
                dest.unlink()
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        digest = file_digest(dest) if dest.is_file() else None
        return CopyOutput(
            src=str(src),
            dest=str(dest),
            sha256=digest,
            is_dir=dest.is_dir(),
            created=created,
            backup_path=backup_path,
        )

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        assert isinstance(params, CopyInput)
        assert isinstance(result, CopyOutput)
        dest = resolve_in_sandbox(params.dest, ctx.allowed_roots)
        if result.created:
            _remove_created(dest)
            return
        _restore_backup(result.backup_path, dest)


class Delete(Tool):
    """Move a path to trash. Never unlinks.

    A single file is L2. A directory (a tree) is L4: blast radius is unbounded
    from the planner's point of view, matching docs/06 §2.
    """

    name: ClassVar[str] = "fs.delete"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Delete a file or directory by moving it to the trash directory, never "
        "unlinking. A directory tree classifies as L4. Cancel restores from trash."
    )
    Input: ClassVar[type[BaseModel]] = DeleteInput
    Output: ClassVar[type[BaseModel]] = DeleteOutput
    base_capability: ClassVar[Capability] = Capability.L2
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "fs"

    def classify(self, params: BaseModel) -> Capability:
        assert isinstance(params, DeleteInput)
        try:
            resolved = Path(params.path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            return Capability.L2
        if resolved.is_dir():
            return Capability.L4
        return Capability.L2

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(result, DeleteOutput)
        return _dest_postconditions(result.trashed_to, result.sha256, result.is_dir)

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, DeleteInput)
        target = resolve_in_sandbox(params.path, ctx.allowed_roots)
        trash_root = _trash_root(ctx)
        slot = trash_root / ctx.action_id
        if not target.exists():
            if slot.exists():
                trashed = _first_child(slot) or slot
                return DeleteOutput(
                    path=str(target),
                    trashed_to=str(trashed),
                    is_dir=trashed.is_dir(),
                    sha256=file_digest(trashed) if trashed.is_file() else None,
                )
            raise ToolError(
                "Path does not exist",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=str(target),
            )
        slot.mkdir(parents=True, exist_ok=True)
        dest = slot / target.name
        if dest.exists():
            _remove_created(dest)
        digest = file_digest(target) if target.is_file() else None
        is_dir = target.is_dir()
        shutil.move(str(target), str(dest))
        return DeleteOutput(
            path=str(target),
            trashed_to=str(dest),
            is_dir=is_dir,
            sha256=digest,
        )

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        assert isinstance(params, DeleteInput)
        assert isinstance(result, DeleteOutput)
        target = resolve_in_sandbox(params.path, ctx.allowed_roots)
        _restore_from_trash(target, Path(result.trashed_to))


def _dest_postconditions(dest: str, digest: str | None, is_dir: bool) -> list[dict[str, Any]]:
    kind = "dir" if is_dir else "file"
    checks: list[dict[str, Any]] = [{"type": "file_exists", "path": dest, "kind": kind, "tier": 1}]
    if digest is not None:
        checks.append({"type": "file_hash", "path": dest, "expected": digest, "tier": 1})
    return checks


def _move_output(
    src: Path, dest: Path, *, overwritten: bool, backup_path: str | None
) -> MoveOutput:
    return MoveOutput(
        src=str(src),
        dest=str(dest),
        sha256=file_digest(dest) if dest.is_file() else None,
        is_dir=dest.is_dir(),
        overwritten=overwritten,
        backup_path=backup_path,
    )


def _backup_existing(target: Path, ctx: ToolContext) -> str | None:
    if not target.exists():
        return None
    backup = ctx.scratch_dir / "backups" / ctx.action_id
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        _remove_created(backup)
    if target.is_dir():
        shutil.copytree(target, backup)
    else:
        shutil.copy2(target, backup)
    return str(backup)


def _restore_backup(backup_path: str | None, dest: Path) -> None:
    if backup_path is None:
        return
    backup = Path(backup_path)
    if not backup.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        _remove_created(dest)
    if backup.is_dir():
        shutil.copytree(backup, dest)
    else:
        shutil.copy2(backup, dest)


def _remove_created(path: Path) -> None:
    """Undo a path this tool created. Not used for user data (that goes to trash)."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink(missing_ok=True)


def _trash_root(ctx: ToolContext) -> Path:
    root = ctx.trash_dir
    if root is None:
        raise ToolError(
            "Trash directory is not configured",
            code=ErrorCode.INTERNAL,
        )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _restore_from_trash(target: Path, trashed: Path) -> None:
    if not trashed.exists() or target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(trashed), str(target))


def _first_child(path: Path) -> Path | None:
    try:
        return next(path.iterdir())
    except (OSError, StopIteration):
        return None


def _entry(path: Path) -> DirEntry:
    if path.is_dir():
        return DirEntry(name=path.name, kind="dir")
    if path.is_file():
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        return DirEntry(name=path.name, kind="file", size=size)
    return DirEntry(name=path.name, kind="other")
