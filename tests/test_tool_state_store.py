from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import (
    InvalidToolInvocationTransition,
    StateStore,
    StateStoreError,
    ToolInvocationConflictError,
    ToolInvocationNotFoundError,
)
from agentbus.policy import (
    ToolApprovalDisposition,
    ToolPolicyEngine,
    build_tool_approval_request,
    decide_tool_approval,
)
from agentbus.tools.descriptors import descriptor_map
from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityName,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationContext,
    ToolInvocationStatus,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolResourceUsage,
    ToolResult,
    ToolVersion,
    capability_fingerprint,
    sha256_json,
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


def policy_decision(
    current: ToolInvocation,
    outcome: ToolPolicyOutcome = ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS,
    *,
    approval_id: str | None = None,
) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        outcome=outcome,
        rule_id=(
            "allow.approved_invocation"
            if approval_id is not None
            else "allow.scoped_mutation"
        ),
        reason="Bounded policy result; token=raw-sensitive-policy-reason",
        invocation_id=current.invocation_id,
        invocation_revision=current.invocation_revision,
        capability_fingerprint=capability_fingerprint(
            current.requested_capabilities
        ),
        arguments_sha256=sha256_json(current.arguments),
        constraints=current.requested_capabilities,
        safe_metadata=(
            {"approval_id": approval_id} if approval_id is not None else {}
        ),
    )


def approval_invocation(
    root: Path,
    invocation_id: str = "approval-invocation-1",
) -> tuple[ToolInvocation, ToolDescriptor]:
    descriptor = descriptor_map(workspace=root, worktree=root)[
        "filesystem.delete"
    ]
    current = ToolInvocation(
        invocation_id=invocation_id,
        run_id="run-1",
        task_id="task-1",
        tool_name=descriptor.name,
        tool_version=descriptor.version,
        arguments={"path": "obsolete.py", "expected_sha256": "0" * 64},
        requested_capabilities=(
            ToolCapability(
                name=ToolCapabilityName.FILESYSTEM_DELETE,
                scope=CapabilityScope(
                    roots=(str(root.resolve()),),
                    affected_paths=("obsolete.py",),
                ),
            ),
        ),
        context=ToolInvocationContext(
            workspace_identity=str(root.resolve()),
            worktree_identity=str(root.resolve()),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
        idempotency_key=f"delete-{invocation_id}",
    )
    return current, descriptor


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


def test_tool_lifecycle_persists_ordered_events_and_safe_replay(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path)
    current = invocation(tmp_path, "invocation-1", idempotency_key=None)
    decision = policy_decision(current)
    store.record_tool_invocation(current)
    allowed = store.record_tool_policy_decision("run-1", decision)
    duplicate_allowed = store.record_tool_policy_decision(
        "run-1",
        decision.model_copy(
            update={"evaluated_at": decision.evaluated_at + timedelta(seconds=1)}
        ),
    )
    running = store.mark_tool_invocation_started("run-1", current.invocation_id)
    result = ToolResult(
        invocation_id=current.invocation_id,
        invocation_revision=current.invocation_revision,
        status=ToolInvocationStatus.SUCCEEDED,
        structured_output={
            "content": "raw-sensitive-structured-output",
            "path": "module.py",
        },
        stdout="Bearer raw-sensitive-stdout-token",
        stderr="api_key=raw-sensitive-stderr-key",
        duration_seconds=0.25,
        resource_usage=ToolResourceUsage(
            stdout_bytes=33,
            stderr_bytes=35,
            file_mutations=1,
            written_bytes=26,
        ),
        policy_decision=decision,
        safe_diagnostic_metadata={"content": "raw-sensitive-diagnostic"},
    )

    completed = store.complete_tool_invocation("run-1", result)
    duplicate = store.complete_tool_invocation("run-1", result)
    delayed_duplicate = store.complete_tool_invocation(
        "run-1",
        result.model_copy(
            update={
                "policy_decision": decision.model_copy(
                    update={
                        "evaluated_at": decision.evaluated_at + timedelta(seconds=2)
                    }
                )
            }
        ),
    )

    assert allowed == duplicate_allowed
    assert allowed.status == ToolInvocationStatus.REQUESTED
    assert running.status == ToolInvocationStatus.RUNNING
    assert completed == duplicate == delayed_duplicate
    assert completed.status == ToolInvocationStatus.SUCCEEDED
    assert completed.safe_result is not None
    assert completed.safe_result.stdout == ""
    assert completed.safe_result.stderr == ""
    assert completed.safe_result.stdout_truncated is True
    assert completed.safe_result.stderr_truncated is True
    assert completed.safe_result.structured_output == {
        "persisted_summary": True,
        "sha256": completed.safe_result.safe_diagnostic_metadata[
            "structured_output_sha256"
        ],
        "key_count": 2,
    }
    lifecycle_events = [
        event["event_type"]
        for event in store.list_events("run-1")
        if event["event_type"].startswith("tool_")
    ]
    assert lifecycle_events == [
        "tool_invocation_requested",
        "tool_policy_allowed",
        "tool_invocation_started",
        "tool_succeeded",
    ]
    persisted_bytes = b"".join(
        path.read_bytes()
        for path in store.database_path.parent.glob(f"{store.database_path.name}*")
    )
    for forbidden in (
        b"raw-sensitive-structured-output",
        b"raw-sensitive-stdout-token",
        b"raw-sensitive-stderr-key",
        b"raw-sensitive-diagnostic",
        b"raw-sensitive-policy-reason",
    ):
        assert forbidden not in persisted_bytes


def test_policy_denial_is_terminal_and_idempotent(tmp_path: Path) -> None:
    store = create_store(tmp_path)
    current = invocation(tmp_path, "invocation-1", idempotency_key=None)
    denied = policy_decision(current, ToolPolicyOutcome.DENY)
    store.record_tool_invocation(current)

    first = store.record_tool_policy_decision("run-1", denied)
    duplicate = store.record_tool_policy_decision("run-1", denied)

    assert first == duplicate
    assert first.status == ToolInvocationStatus.DENIED
    assert first.error_category.value == "policy_denied"
    assert first.completed_at is not None
    with pytest.raises(InvalidToolInvocationTransition, match="terminal"):
        store.mark_tool_invocation_started("run-1", current.invocation_id)
    denied_events = [
        event
        for event in store.list_events("run-1")
        if event["event_type"] == "tool_policy_denied"
    ]
    assert len(denied_events) == 1


def test_policy_and_result_bindings_reject_cross_invocation_reuse(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path)
    first = invocation(tmp_path, "invocation-1", idempotency_key=None)
    second = invocation(
        tmp_path,
        "invocation-2",
        path="second.py",
        idempotency_key=None,
    )
    store.record_tool_invocation(first)
    store.record_tool_invocation(second)

    with pytest.raises(ToolInvocationConflictError, match="policy decision"):
        store.record_tool_policy_decision(
            "run-1",
            policy_decision(second).model_copy(
                update={"invocation_id": first.invocation_id}
            ),
        )

    first_decision = policy_decision(first)
    store.record_tool_policy_decision("run-1", first_decision)
    with pytest.raises(InvalidToolInvocationTransition, match="running state"):
        store.complete_tool_invocation(
            "run-1",
            ToolResult(
                invocation_id=first.invocation_id,
                invocation_revision=first.invocation_revision,
                status=ToolInvocationStatus.SUCCEEDED,
                policy_decision=first_decision,
            ),
        )


def test_tool_approval_round_trips_and_allows_exact_invocation(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path)
    current, descriptor = approval_invocation(tmp_path)
    engine = ToolPolicyEngine()
    decision = engine.evaluate(current, descriptor)
    request = build_tool_approval_request(
        current,
        descriptor,
        decision,
        approval_id="approval-1",
    )
    store.record_tool_invocation(current)
    waiting = store.record_tool_policy_decision(
        "run-1",
        decision,
        approval_id=request.approval_id,
    )

    pending = store.record_tool_approval_request(request, current, descriptor)
    regenerated_request = build_tool_approval_request(
        current,
        descriptor,
        decision,
        approval_id=request.approval_id,
    )
    duplicate_pending = store.record_tool_approval_request(
        regenerated_request,
        current,
        descriptor,
    )
    grant = decide_tool_approval(
        request,
        current,
        disposition=ToolApprovalDisposition.APPROVED,
        reason="approved; token=raw-sensitive-approval-reason",
    )
    approved = store.record_tool_approval_grant(grant, current, descriptor)
    duplicate_approved = store.record_tool_approval_grant(
        grant,
        current,
        descriptor,
    )
    final_decision = engine.evaluate(current, descriptor, approval=grant)
    store.record_tool_policy_decision(
        "run-1",
        final_decision,
        approval_id=request.approval_id,
    )
    started = store.mark_tool_invocation_started(
        "run-1",
        current.invocation_id,
        approval_id=request.approval_id,
    )

    assert waiting.status == ToolInvocationStatus.AWAITING_APPROVAL
    assert pending == duplicate_pending
    assert approved == duplicate_approved
    assert approved.disposition == "approved"
    assert approved.reason == "approved; token=[REDACTED]"
    assert store.get_tool_approval("run-1", "approval-1") == approved
    assert store.list_tool_approvals("run-1", pending_only=True) == []
    assert started.status == ToolInvocationStatus.RUNNING
    assert started.approval_id == "approval-1"
    persisted_bytes = b"".join(
        path.read_bytes()
        for path in store.database_path.parent.glob(f"{store.database_path.name}*")
    )
    assert b"raw-sensitive-approval-reason" not in persisted_bytes


def test_tool_approval_rejects_changed_resource_summary_and_grant(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path)
    current, descriptor = approval_invocation(tmp_path)
    decision = ToolPolicyEngine().evaluate(current, descriptor)
    request = build_tool_approval_request(
        current,
        descriptor,
        decision,
        approval_id="approval-1",
    )
    store.record_tool_invocation(current)
    with pytest.raises(ToolInvocationConflictError, match="stable approval ID"):
        store.record_tool_policy_decision("run-1", decision)
    store.record_tool_policy_decision(
        "run-1", decision, approval_id=request.approval_id
    )

    with pytest.raises(ToolInvocationConflictError, match="resource summary"):
        store.record_tool_approval_request(
            request.model_copy(update={"affected_paths": ("different.py",)}),
            current,
            descriptor,
        )
    with pytest.raises(ToolInvocationConflictError, match="persisted invocation"):
        store.record_tool_approval_request(
            request,
            current.model_copy(
                update={"arguments": {"path": "different.py"}}
            ),
            descriptor,
        )

    store.record_tool_approval_request(request, current, descriptor)
    fabricated_allow = decision.model_copy(
        update={
            "outcome": ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS,
            "rule_id": "allow.approved_invocation",
            "safe_metadata": {"approval_id": request.approval_id},
        }
    )
    with pytest.raises(ToolInvocationConflictError, match="has not been approved"):
        store.record_tool_policy_decision(
            "run-1",
            fabricated_allow,
            approval_id=request.approval_id,
        )
    approved = decide_tool_approval(
        request,
        current,
        disposition=ToolApprovalDisposition.APPROVED,
    )
    store.record_tool_approval_grant(approved, current, descriptor)
    with pytest.raises(ToolInvocationConflictError, match="cannot be replaced"):
        store.record_tool_approval_grant(
            approved.model_copy(
                update={"disposition": ToolApprovalDisposition.REJECTED}
            ),
            current,
            descriptor,
        )


def test_rejected_tool_approval_transitions_invocation_to_denied(
    tmp_path: Path,
) -> None:
    store = create_store(tmp_path)
    current, descriptor = approval_invocation(tmp_path)
    engine = ToolPolicyEngine()
    decision = engine.evaluate(current, descriptor)
    request = build_tool_approval_request(
        current,
        descriptor,
        decision,
        approval_id="approval-1",
    )
    store.record_tool_invocation(current)
    store.record_tool_policy_decision(
        "run-1", decision, approval_id=request.approval_id
    )
    store.record_tool_approval_request(request, current, descriptor)
    rejected = decide_tool_approval(
        request,
        current,
        disposition=ToolApprovalDisposition.REJECTED,
        reason="not approved",
    )

    stored_rejection = store.record_tool_approval_grant(
        rejected,
        current,
        descriptor,
    )
    denied = store.record_tool_policy_decision(
        "run-1",
        engine.evaluate(current, descriptor, approval=rejected),
        approval_id=request.approval_id,
    )

    assert stored_rejection.disposition == "rejected"
    assert denied.status == ToolInvocationStatus.DENIED
    with pytest.raises(InvalidToolInvocationTransition, match="terminal"):
        store.mark_tool_invocation_started(
            "run-1",
            current.invocation_id,
            approval_id=request.approval_id,
        )
