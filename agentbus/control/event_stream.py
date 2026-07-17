from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Any

from agentbus.control.models import EventEnvelope
from agentbus.execution.state_store import StateStore
from agentbus.security.redaction import sanitize_json

DEFAULT_EVENT_LIMIT = 250
MAX_EVENT_PAYLOAD_CHARS = 8_192


def parse_event_cursor(value: str | int | None) -> int:
    if value in (None, ""):
        return 0
    try:
        cursor = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Last-Event-ID must be a non-negative integer.") from exc
    if cursor < 0:
        raise ValueError("Last-Event-ID must be a non-negative integer.")
    return cursor


class ControlEventReader:
    def __init__(self, store: StateStore):
        self.store = store

    def read(
        self,
        *,
        after_sequence: int = 0,
        run_id: str | None = None,
        limit: int = DEFAULT_EVENT_LIMIT,
    ) -> list[EventEnvelope]:
        rows = (
            self.store.list_events(
                run_id,
                after_event_id=after_sequence,
                limit=limit,
            )
            if run_id
            else self.store.list_all_events(
                after_event_id=after_sequence,
                limit=limit,
            )
        )
        return [self._to_event(row) for row in rows]

    @staticmethod
    def _to_event(row: dict[str, Any]) -> EventEnvelope:
        payload = sanitize_json(
            row.get("payload", {}),
            max_chars=MAX_EVENT_PAYLOAD_CHARS,
        )
        if not isinstance(payload, dict):
            payload = {"value": payload}
        timestamp = row.get("created_at") or datetime.now(timezone.utc)
        return EventEnvelope(
            sequence=int(row["event_id"]),
            event_type=str(row["event_type"]),
            timestamp=timestamp,
            run_id=str(row["run_id"]) if row.get("run_id") else None,
            task_id=str(row["task_id"]) if row.get("task_id") else None,
            worker_id=_worker_id(payload),
            payload=payload,
        )


def encode_sse(event: EventEnvelope) -> str:
    data = json.dumps(
        event.model_dump(mode="json", exclude_none=True),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


async def stream_events(
    reader: ControlEventReader,
    *,
    after_sequence: int = 0,
    run_id: str | None = None,
    disconnected: Callable[[], Any] | None = None,
    poll_seconds: float = 0.25,
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[str]:
    cursor = after_sequence
    elapsed = 0.0
    while True:
        if disconnected is not None and await _maybe_await(disconnected()):
            return
        events = await asyncio.to_thread(
            reader.read,
            after_sequence=cursor,
            run_id=run_id,
        )
        if events:
            elapsed = 0.0
            for event in events:
                if event.sequence <= cursor:
                    continue
                cursor = event.sequence
                yield encode_sse(event)
            continue
        await asyncio.sleep(poll_seconds)
        elapsed += poll_seconds
        if elapsed >= heartbeat_seconds:
            elapsed = 0.0
            yield ": heartbeat\n\n"


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _worker_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("worker_id")
    return str(value) if value else None
