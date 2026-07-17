from __future__ import annotations

import asyncio
import json

import pytest

from agentbus.control.event_stream import (
    ControlEventReader,
    encode_sse,
    parse_event_cursor,
    stream_events,
)
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore


def _store(tmp_path) -> StateStore:
    store = StateStore(tmp_path / "state.db")
    store.create_run(
        RunRecord(
            run_id="run-1",
            original_task="Task",
            model="fake",
            workspace=str(tmp_path),
        )
    )
    return store


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 0), ("", 0), ("0", 0), ("42", 42), (42, 42)],
)
def test_event_cursor_parsing(value, expected) -> None:
    assert parse_event_cursor(value) == expected


@pytest.mark.parametrize("value", ["invalid", "-1", -1])
def test_invalid_event_cursor_is_rejected(value) -> None:
    with pytest.raises(ValueError, match="Last-Event-ID"):
        parse_event_cursor(value)


def test_reader_replays_ordered_redacted_events(tmp_path) -> None:
    store = _store(tmp_path)
    first = store.record_event("run-1", "run_started", {"token": "secret"})
    second = store.record_event("run-1", "run_succeeded", {"worker_id": "worker-1"})
    reader = ControlEventReader(store)

    events = reader.read(after_sequence=first)

    assert [event.sequence for event in events] == [second]
    assert events[0].worker_id == "worker-1"
    assert "secret" not in json.dumps(
        reader.read()[0].model_dump(mode="json")
    )


def test_sse_encoding_contains_id_type_and_single_json_payload(tmp_path) -> None:
    store = _store(tmp_path)
    store.record_event("run-1", "run_started", {"safe": True})
    event = ControlEventReader(store).read()[-1]

    encoded = encode_sse(event)

    assert f"id: {event.sequence}\n" in encoded
    assert "event: run_started\n" in encoded
    assert encoded.endswith("\n\n")
    assert json.loads(
        next(line[6:] for line in encoded.splitlines() if line.startswith("data: "))
    )["run_id"] == "run-1"


def test_stream_replays_then_stops_when_client_disconnects(tmp_path) -> None:
    store = _store(tmp_path)
    store.record_event("run-1", "run_started")
    calls = 0

    async def disconnected() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in stream_events(
                ControlEventReader(store),
                disconnected=disconnected,
                poll_seconds=0.001,
            )
        ]

    chunks = asyncio.run(collect())

    assert len(chunks) >= 1
    assert "event: run_started" in "".join(chunks)
