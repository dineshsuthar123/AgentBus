from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import (
    StateStore,
    StateStoreError,
    ToolInvocationConflictError,
    ToolInvocationNotFoundError,
)
from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityName,
    ToolInvocation,
    ToolInvocationContext,
    ToolInvocationStatus,
    ToolResourceUsage,
    ToolVersion,
)


def create_store(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.db")
    store.create_run_with_tasks(
        RunRecord(
            run_id="run-1",
            original_task="Managed tool request",
            model="fake",
            workspace=str(tmp_path.resolve()),
            planner_output={"goal": "test", "steps": []},
            graph_data={"version": 1, "tasks": []},
        ),
        [
            TaskSpec(
                task_id="task-1",
                title="Invoke tool",
                description="Exercise persistence",
            ),
            TaskSpec(
                task_id="task-2",
                title="Invoke another tool",
                description="Exercise filtering",
            ),
        ],
    )
    return store


def invocation(
    root: Path,
    invocation_id: str,
    *,
    task_id: str = "task-1",
    path: str = "module.py",
    idempotency_key: str | None = "idem-sensitive-value",
) -> ToolInvocation:
    return ToolInvocation(
        invocation_id=invocation_id,
        run_id="run-1",
        task_id=task_id,
        tool_name="filesystem.write",
        tool_version=ToolVersion(major=1),
        arguments={"path": path, "content": "raw-sensitive-file-content"},
        requested_capabilities=(
            ToolCapability(
                name=ToolCapabilityName.FILESYSTEM_WRITE,
                scope=CapabilityScope(roots=(str(root.resolve()),)),
            ),
        ),
        context=ToolInvocationContext(
            workspace_identity=str(root.resolve()),
            worktree_identity=str(root.resolve()),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
            policy_context={"api_key": "raw-sensitive-policy-key"},
        ),
        idempotency_key=idempotency_key,
    )


def test_tool_invocation_round_trips_without_raw_arguments_or_keys(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path)
    current = invocation(tmp_path, "invocation-1")

    created = store.record_tool_invocation(
        current,
        anticipated_usage=ToolResourceUsage(file_mutations=1, written_bytes=26),
    )
    reopened = StateStore(store.database_path).get_tool_invocation(
        "run-1", "invocation-1"
    )

    assert created == reopened
    assert reopened.status == ToolInvocationStatus.REQUESTED
    assert reopened.anticipated_usage.file_mutations == 1
    assert reopened.arguments_sha256 != current.arguments["content"]
    assert reopened.idempotency_key_sha256 != current.idempotency_key
    database_bytes = b"".join(
        path.read_bytes()
        for path in store.database_path.parent.glob(f"{store.database_path.name}*")
    )
    for forbidden in (
        b"raw-sensitive-file-content",
        b"raw-sensitive-policy-key",
        b"idem-sensitive-value",
    ):
        assert forbidden not in database_bytes


def test_exact_duplicate_is_idempotent_across_request_timestamps(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path)
    current = invocation(tmp_path, "invocation-1", idempotency_key=None)
    first = store.record_tool_invocation(current)

    duplicate = store.record_tool_invocation(
        current.model_copy(
            update={"requested_at": current.requested_at + timedelta(seconds=5)}
        )
    )

    assert duplicate == first
    assert len(store.list_tool_invocations("run-1")) == 1
    requested = [
        event
        for event in store.list_events("run-1")
        if event["event_type"] == "tool_invocation_requested"
    ]
    assert len(requested) == 1


def test_invocation_identity_rejects_argument_and_reservation_changes(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path)
    current = invocation(tmp_path, "invocation-1", idempotency_key=None)
    store.record_tool_invocation(current)

    with pytest.raises(ToolInvocationConflictError):
        store.record_tool_invocation(
            current.model_copy(
                update={"arguments": {"path": "other.py", "content": "changed"}}
            )
        )
    with pytest.raises(ToolInvocationConflictError):
        store.record_tool_invocation(
            current,
            anticipated_usage=ToolResourceUsage(file_mutations=1),
        )
    with pytest.raises(ToolInvocationConflictError):
        store.record_tool_invocation(current, process_slot=True)


def test_idempotency_key_deduplicates_only_the_same_operation(tmp_path: Path) -> None:
    store = create_store(tmp_path)
    first = store.record_tool_invocation(invocation(tmp_path, "invocation-1"))

    replay = store.record_tool_invocation(invocation(tmp_path, "invocation-2"))

    assert replay == first
    assert replay.invocation_id == "invocation-1"
    with pytest.raises(ToolInvocationConflictError):
        store.record_tool_invocation(
            invocation(tmp_path, "invocation-3", path="expanded.py")
        )


def test_tool_invocation_listing_is_ordered_filtered_and_bounded(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path)
    first = store.record_tool_invocation(
        invocation(tmp_path, "invocation-1", idempotency_key=None)
    )
    second = store.record_tool_invocation(
        invocation(
            tmp_path,
            "invocation-2",
            task_id="task-2",
            idempotency_key=None,
        )
    )

    assert store.list_tool_invocations(
        "run-1", after_sequence=first.invocation_sequence
    ) == [second]
    assert store.list_tool_invocations("run-1", task_id="task-1") == [first]
    assert store.list_tool_invocations(
        "run-1", status=ToolInvocationStatus.REQUESTED
    ) == [first, second]
    with pytest.raises(StateStoreError, match="page limit"):
        store.list_tool_invocations("run-1", limit=1001)
    with pytest.raises(StateStoreError, match="cursor"):
        store.list_tool_invocations("run-1", after_sequence=-1)
    with pytest.raises(ToolInvocationNotFoundError):
        store.get_tool_invocation("run-1", "missing")


def test_corrupt_tool_protocol_state_fails_closed(tmp_path: Path) -> None:
    store = create_store(tmp_path)
    store.record_tool_invocation(
        invocation(tmp_path, "invocation-1", idempotency_key=None)
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE tool_invocations SET capabilities_json = 'not-json'"
        )
        connection.commit()

    with pytest.raises(StateStoreError, match="recovery cannot continue safely"):
        store.get_tool_invocation("run-1", "invocation-1")
