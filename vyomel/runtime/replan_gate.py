"""Optional replan hook invoked before a task fails (FR-106)."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.store.models import Action, Step, Task


class ReplanGate(Protocol):
    async def try_replan(
        self,
        session: AsyncSession,
        task: Task,
        actions: list[Action],
        steps: list[Step],
    ) -> bool: ...


class NullReplanGate:
    async def try_replan(
        self,
        session: AsyncSession,
        task: Task,
        actions: list[Action],
        steps: list[Step],
    ) -> bool:
        return False
