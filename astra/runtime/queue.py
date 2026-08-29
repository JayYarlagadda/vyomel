"""Redis Streams transport.

Postgres is the source of truth; this is the mailbox. A lost stream is
recoverable. A lost row is not. See docs/07-EXECUTION-ENGINE.md section 4.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import ResponseError

DEFAULT_STREAM = "astra:actions"
DEFAULT_GROUP = "workers"


@dataclass(frozen=True, slots=True)
class StreamMessage:
    message_id: str
    action_id: str


class ActionQueue:
    def __init__(
        self,
        redis: Redis,
        *,
        stream: str = DEFAULT_STREAM,
        group: str = DEFAULT_GROUP,
    ) -> None:
        self._redis = redis
        self.stream = stream
        self.group = group

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def publish(self, action_id: str) -> str:
        message_id = await self._redis.xadd(self.stream, {"action_id": action_id})
        return str(message_id)

    async def claim(self, consumer: str, *, block_ms: int = 5_000) -> StreamMessage | None:
        result = await self._redis.xreadgroup(
            self.group,
            consumer,
            {self.stream: ">"},
            count=1,
            block=block_ms,
        )
        return _first(result)

    async def ack(self, message: StreamMessage) -> None:
        await self._redis.xack(self.stream, self.group, message.message_id)

    async def autoclaim(
        self, consumer: str, *, min_idle_ms: int, count: int = 50
    ) -> list[StreamMessage]:
        # redis-py returns (next_id, messages, deleted_ids) on recent versions.
        claimed = await self._redis.xautoclaim(
            self.stream,
            self.group,
            consumer,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )
        if isinstance(claimed, (list, tuple)) and len(claimed) >= 2:
            messages = claimed[1]
        else:
            messages = claimed
        parsed: list[StreamMessage] = []
        for item in messages:
            parsed_one = _parse(item)
            if parsed_one is not None:
                parsed.append(parsed_one)
        return parsed


def _first(result: object) -> StreamMessage | None:
    if not result:
        return None
    # [[stream, [(id, {field: value})]]]
    try:
        entries = result[0][1]  # type: ignore[index]
    except (IndexError, TypeError):
        return None
    if not entries:
        return None
    return _parse(entries[0])


def _parse(entry: object) -> StreamMessage | None:
    if not isinstance(entry, (list, tuple)) or len(entry) != 2:
        return None
    message_id, fields = entry
    if not isinstance(fields, dict) or "action_id" not in fields:
        return None
    action_id = fields["action_id"]
    if isinstance(action_id, bytes):
        action_id = action_id.decode()
    if isinstance(message_id, bytes):
        message_id = message_id.decode()
    return StreamMessage(message_id=str(message_id), action_id=str(action_id))
