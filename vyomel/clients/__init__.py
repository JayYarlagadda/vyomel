"""Thin external clients of the Vyomel HTTP API (wearable, etc.)."""

from vyomel.clients.wearable import WearableClient, task_create_payload

__all__ = ["WearableClient", "task_create_payload"]
