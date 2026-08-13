from __future__ import annotations

import errno
from pathlib import Path
from typing import BinaryIO, TextIO

import pytest

from agentbus.evaluation import storage as evaluation_storage
from agentbus.evaluation.errors import EvaluationStorageError
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.intelligence.errors import IndexUnavailableError
from agentbus.intelligence.storage import IndexStore
from agentbus.product import daemon as daemon_module
from agentbus.product import logging as product_logging
from agentbus.sandbox import process as process_module
from agentbus.sandbox.errors import ProcessSupervisionError
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.sandbox.process import ControlledProcessSupervisor
from agentbus.tools import filesystem_operations
from agentbus.tools.filesystem_operations import ContainedFileSystem
from agentbus.trace import (
    ContentAddressedStore,
    RuntimeTrace,
    TraceEventType,
    TraceStatus,
    TraceStorageError,
)


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


def test_unavailable_log_directory_is_actionable_without_rewriting_lifecycle(
    tmp_path: Path,
) -> None:
    state, expected = _durable_state(tmp_path)
    blocker = tmp_path / "log-parent-is-a-file"
    blocker.write_text("block directory creation", encoding="utf-8")
    path = blocker / "agentbus.log"

    with pytest.raises(
        OSError,
        match="log directory is writable and has available disk space",
    ) as captured:
        product_logging.ProductLogWriter(path).write(
            level="info",
            component="storage-matrix",
            message="write bounded diagnostic",
        )

    assert isinstance(captured.value, product_logging.ProductLogError)
    with pytest.warns(
        RuntimeWarning,
        match="log directory is writable and has available disk space",
    ):
        assert daemon_module._append_lifecycle_log(path, "started") is False
    assert state.get_run(expected.run_id) == expected


def test_unavailable_process_temporary_directory_is_actionable_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, expected = _durable_state(tmp_path)
    spawn_attempted = False

    def unavailable_temporary_directory(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "injected temporary storage exhaustion")

    def fail_spawn(*args: object, **kwargs: object) -> None:
        nonlocal spawn_attempted
        spawn_attempted = True
        raise AssertionError("process spawn must not be attempted")

    monkeypatch.setattr(
        process_module.tempfile,
        "TemporaryDirectory",
        unavailable_temporary_directory,
    )
    monkeypatch.setattr(process_module.subprocess, "Popen", fail_spawn)
    supervisor = ControlledProcessSupervisor(
        expected.workspace,
        catalog=ExecutableCatalog.standard(("python",)),
    )

    with pytest.raises(
        ProcessSupervisionError,
        match="temporary directory is writable and has available space",
    ):
        supervisor.run("python", ("-c", "print('must not run')"))

    assert spawn_attempted is False
    assert state.get_run(expected.run_id) == expected


def test_interrupted_evaluation_artifact_preserves_original_and_cleans_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, expected = _durable_state(tmp_path)
    target = Path(expected.workspace) / "report.json"
    original = '{"status": "original"}\n'
    target.write_text(original, encoding="utf-8")
    real_named_temporary_file = evaluation_storage.tempfile.NamedTemporaryFile

    def interrupted_named_temporary_file(*args, **kwargs):
        return _InterruptedTextHandle(real_named_temporary_file(*args, **kwargs))

    monkeypatch.setattr(
        evaluation_storage.tempfile,
        "NamedTemporaryFile",
        interrupted_named_temporary_file,
    )

    with pytest.raises(
        EvaluationStorageError,
        match="destination is writable and has available disk space",
    ):
        evaluation_storage._write_json(target, {"status": "replacement"})

    assert target.read_text(encoding="utf-8") == original
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []
    assert state.get_run(expected.run_id) == expected


def test_interrupted_trace_metadata_removes_uncommitted_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, expected = _durable_state(tmp_path)
    root = tmp_path / "trace-store"
    store = ContentAddressedStore(root)
    real_atomic_write = store._atomic_write

    def interrupt_metadata(destination: Path, value: bytes) -> None:
        if destination.suffix == ".json":
            raise TraceStorageError("injected metadata publication interruption")
        real_atomic_write(destination, value)

    monkeypatch.setattr(store, "_atomic_write", interrupt_metadata)

    with pytest.raises(TraceStorageError, match="metadata publication interruption"):
        store.put_text("safe trace artifact", producing_span_id="storage-span")

    assert [path for path in store.blob_directory.rglob("*") if path.is_file()] == []
    assert [
        path for path in store.metadata_directory.rglob("*") if path.is_file()
    ] == []
    assert list(root.rglob(".agentbus-tmp-*")) == []
    assert state.get_run(expected.run_id) == expected


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


class _InterruptedTextHandle:
    def __init__(self, handle: TextIO) -> None:
        self._handle = handle

    @property
    def name(self) -> str:
        return self._handle.name

    def __enter__(self) -> "_InterruptedTextHandle":
        self._handle.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self._handle.__exit__(*args)

    def write(self, value: str) -> int:
        self._handle.write(value[:8])
        self._handle.flush()
        raise OSError(errno.ENOSPC, "injected artifact write interruption")

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
