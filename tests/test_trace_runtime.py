from pathlib import Path

from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.trace import (
    RuntimeTrace,
    TraceSpanType,
    TraceStatus,
)


def _run(workspace: Path, run_id: str = "run-1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        original_task="Trace the durable runtime",
        model="deterministic",
        workspace=str(workspace),
    )


def test_runtime_trace_persists_sanitized_objects_and_checkpoints(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = StateStore(tmp_path / "state.db")
    store.create_run(_run(workspace))
    runtime = RuntimeTrace.open(
        store,
        "run-1",
        object_root=tmp_path / "objects",
        workspace=workspace,
        root_attributes={"durable": True},
    )
    task = runtime.start_span(
        TraceSpanType.TASK,
        "step one",
        task_id="step-1",
    )
    output = runtime.capture_json_output(
        task,
        "task.result",
        {
            "password": "never-store-this",
            "workspace": str(workspace),
            "status": "succeeded",
        },
    )
    runtime.finish_span(
        task,
        output_references=[output] if output is not None else [],
    )
    checkpoint = runtime.checkpoint(
        "task-complete",
        {"completed_tasks": ["step-1"]},
    )
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)

    assert trace is not None
    assert checkpoint is not None
    assert output is not None
    stored = runtime.object_store.get(output.sha256)
    assert b"never-store-this" not in stored.data
    assert str(workspace).encode() not in stored.data
    assert store.get_run_trace("run-1") == trace


def test_runtime_trace_reconciles_abandoned_span_after_restart(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = StateStore(tmp_path / "state.db")
    store.create_run(_run(workspace))
    initial = RuntimeTrace.open(
        store,
        "run-1",
        object_root=tmp_path / "objects",
        workspace=workspace,
    )
    abandoned = initial.start_span(
        TraceSpanType.TASK,
        "abandoned",
        task_id="step-1",
    )

    resumed = RuntimeTrace.open(
        StateStore(tmp_path / "state.db"),
        "run-1",
        object_root=tmp_path / "objects",
        workspace=workspace,
    )
    trace = resumed.snapshot()

    assert trace is not None
    restored = next(span for span in trace.spans if span.span_id == abandoned.span_id)
    assert restored.status == TraceStatus.INTERRUPTED
    assert any(event.event_type == "trace.reconciled" for event in trace.events)


def test_terminal_runtime_trace_is_read_only_on_reopen(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = StateStore(tmp_path / "state.db")
    store.create_run(_run(workspace))
    initial = RuntimeTrace.open(
        store,
        "run-1",
        object_root=tmp_path / "objects",
        workspace=workspace,
    )
    expected = initial.finish(status=TraceStatus.SUCCEEDED)

    restored = RuntimeTrace.open(
        StateStore(tmp_path / "state.db"),
        "run-1",
        object_root=tmp_path / "objects",
        workspace=workspace,
    )

    assert restored.active is False
    assert restored.snapshot() == expected
    assert restored.start_span(TraceSpanType.TASK, "ignored") is None
