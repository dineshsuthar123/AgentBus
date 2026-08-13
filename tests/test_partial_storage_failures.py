from __future__ import annotations

import errno
from pathlib import Path
from typing import BinaryIO

import pytest

from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.intelligence.errors import IndexUnavailableError
from agentbus.intelligence.storage import IndexStore
from agentbus.tools import filesystem_operations
from agentbus.tools.filesystem_operations import ContainedFileSystem
from agentbus.trace import RuntimeTrace, TraceEventType, TraceStatus


_ATTRIBUTION = {"task_id": "storage-matrix", "invocation_id": "invocation-1"}


def test_unavailable_trace_directory_preserves_durable_run_and_trace(
    tmp_path: Path,
) -> None:
    state, expected = _durable_state(tmp_path)
    blocker = tmp_path / "trace-parent-is-a-file"
    blocker.write_text("block directory creation", encoding="utf-8")

    runtime = RuntimeTrace.open(
        state,
        expected.run_id,
        object_root=blocker / "objects",
        workspace=expected.workspace,
    )
    trace = runtime.snapshot()

    assert runtime.active is True
    assert runtime.object_store is None
    assert trace is not None
    assert trace.status == TraceStatus.RUNNING
    assert trace.attributes["recording_degraded"] is True
    assert any(
        event.event_type == TraceEventType.RECORDING_DEGRADED
        for event in trace.events
    )
    restored = StateStore(tmp_path / "state.db")
    assert restored.get_run(expected.run_id) == expected
    restored_trace = restored.get_run_trace(expected.run_id)
    assert restored_trace.trace_id == trace.trace_id
    assert restored_trace.status == TraceStatus.RUNNING
    assert any(
        event.event_type == TraceEventType.RECORDING_DEGRADED
        for event in restored_trace.events
    )


def test_unavailable_index_storage_does_not_mutate_durable_state(
    tmp_path: Path,
) -> None:
    state, expected = _durable_state(tmp_path)
    events_before = state.list_events(expected.run_id)
    blocker = tmp_path / "index-parent-is-a-file"
    blocker.write_text("block directory creation", encoding="utf-8")

    with pytest.raises(IndexUnavailableError, match="Unable to create index directory"):
        IndexStore(blocker / "repository-index.sqlite3")

    assert state.get_run(expected.run_id) == expected
    assert state.list_events(expected.run_id) == events_before


def test_quota_interruption_preserves_original_and_cleans_atomic_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, expected = _durable_state(tmp_path)
    workspace = Path(expected.workspace)
    target = workspace / "important.txt"
    target.write_text("original", encoding="utf-8")
    filesystem = ContainedFileSystem(workspace)
    real_fdopen = filesystem_operations.os.fdopen

    def quota_fdopen(descriptor: int, mode: str) -> _QuotaHandle:
        return _QuotaHandle(real_fdopen(descriptor, mode), quota_bytes=4)

    monkeypatch.setattr(filesystem_operations.os, "fdopen", quota_fdopen)

    with pytest.raises(OSError) as captured:
        filesystem.write("important.txt", "replacement", **_ATTRIBUTION)

    assert captured.value.errno == errno.ENOSPC
    assert target.read_text(encoding="utf-8") == "original"
    assert list(workspace.glob(".important.txt.agentbus-*.tmp")) == []
    assert state.get_run(expected.run_id) == expected


class _QuotaHandle:
    def __init__(self, handle: BinaryIO, *, quota_bytes: int) -> None:
        self._handle = handle
        self._quota_bytes = quota_bytes

    def __enter__(self) -> "_QuotaHandle":
        return self

    def __exit__(self, *args: object) -> None:
        self._handle.close()

    def write(self, value: bytes) -> int:
        self._handle.write(value[: self._quota_bytes])
        self._handle.flush()
        raise OSError(errno.ENOSPC, "injected bounded quota exhausted")

    def flush(self) -> None:
        self._handle.flush()

    def fileno(self) -> int:
        return self._handle.fileno()


def _durable_state(tmp_path: Path) -> tuple[StateStore, RunRecord]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    run = RunRecord(
        run_id="storage-run",
        original_task="Exercise bounded storage failure handling",
        model="deterministic",
        workspace=str(workspace.resolve()),
    )
    store = StateStore(tmp_path / "state.db")
    store.create_run(run)
    return store, run
