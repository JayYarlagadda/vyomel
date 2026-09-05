"""Fixture OpenAI-compatible server for serving benchmarks (FR-707).

Two modes share one prompt set:

* ``baseline`` — naive sequential serving: only one request runs at a time
  (HF ``transformers`` generate loop shape).
* ``vllm`` — continuous batching: concurrent requests share a worker pool,
  so aggregate tokens/sec rises with concurrency until ``max_num_seqs``.

This is the CI stand-in for a rented GPU. Live numbers replace the fixture
table after ``infra/vllm/up.ps1`` on an A10G/L4.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel


class _Message(BaseModel):
    role: str
    content: str


class _ChatRequest(BaseModel):
    model: str = "fixture"
    messages: list[_Message]
    temperature: float = 0.0
    seed: int | None = None
    max_tokens: int = 64
    response_format: dict[str, Any] | None = None


def build_app(
    *,
    mode: Literal["baseline", "vllm"] = "baseline",
    max_num_seqs: int = 8,
    prefill_ms: float = 40.0,
    decode_ms_per_token: float = 12.0,
) -> FastAPI:
    app = FastAPI(title=f"vyomel-serving-fixture-{mode}")
    lock = asyncio.Lock()
    slots = asyncio.Semaphore(1 if mode == "baseline" else max_num_seqs)
    stats: dict[str, Any] = {"requests": 0, "mode": mode, "max_inflight": 0, "inflight": 0}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "mode": mode}

    @app.get("/v1/stats")
    async def get_stats() -> dict[str, Any]:
        return dict(stats)

    @app.post("/v1/chat/completions")
    async def chat(req: _ChatRequest) -> dict[str, Any]:
        tokens = max(8, min(req.max_tokens, 64))
        async with slots:
            async with lock:
                stats["inflight"] += 1
                stats["max_inflight"] = max(stats["max_inflight"], stats["inflight"])
                stats["requests"] += 1
            try:
                await asyncio.sleep(prefill_ms / 1000.0)
                await asyncio.sleep(tokens * decode_ms_per_token / 1000.0)
            finally:
                async with lock:
                    stats["inflight"] -= 1
        content = ("ok " * tokens).strip()
        prompt_tokens = max(1, sum(len(m.content) for m in req.messages) // 4)
        return {
            "id": f"chatcmpl-fixture-{stats['requests']}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": tokens,
                "total_tokens": prompt_tokens + tokens,
            },
        }

    return app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FixtureServer:
    """Run the fixture app on an ephemeral localhost port."""

    def __init__(self, *, mode: Literal["baseline", "vllm"], max_num_seqs: int = 8) -> None:
        self.mode = mode
        self.max_num_seqs = max_num_seqs
        self.base_url = ""
        self._server: Any = None
        self._task: asyncio.Task[Any] | None = None

    async def __aenter__(self) -> FixtureServer:
        import uvicorn

        port = _free_port()
        app = build_app(mode=self.mode, max_num_seqs=self.max_num_seqs)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self.base_url = f"http://127.0.0.1:{port}/v1"
        self._task = asyncio.create_task(self._server.serve())
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            if self._server.started:
                return self
            await asyncio.sleep(0.02)
        raise RuntimeError(f"fixture server ({self.mode}) failed to start on {port}")

    async def __aexit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
