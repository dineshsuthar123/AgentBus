from __future__ import annotations

import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from agentbus.execution.cancellation import CancellationRequested, CancellationToken
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.policy import (
    ToolApprovalDisposition,
    ToolPolicyEngine,
    decide_tool_approval,
)
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools import builtin_tool_registry
from agentbus.tools.budget import ToolBudgetExceeded
from agentbus.tools.capabilities import (
    anticipated_tool_usage,
    derive_required_capabilities,
    requires_process_slot,
)
from agentbus.tools.dispatcher import ToolDispatcher
from agentbus.tools.protocol import (
    ToolInvocation,
    ToolInvocationContext,
    ToolInvocationStatus,
    ToolErrorCategory,
    ToolResourceBudget,
)


def test_dispatcher_executes_and_audits_real_managed_mutation(
    tmp_path: Path,
) -> None:
    dispatcher, store = _runtime(tmp_path)
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.create",
        {"path": "src/module.py", "content": "value = 1\n"},
    )

    response = dispatcher.dispatch(invocation)

    assert response.result is not None
    assert response.result.status == ToolInvocationStatus.SUCCEEDED
    assert response.audit is not None
    assert response.audit.record.arguments_sha256
    assert response.record.safe_result is not None
    assert response.record.safe_result.structured_output["persisted_summary"] is True
    assert (tmp_path / "src" / "module.py").read_text(encoding="utf-8") == (
        "value = 1\n"
    )
    assert len(store.list_tool_audits("run-1")) == 1
    event_types = [event["event_type"] for event in store.list_events("run-1")]
    assert event_types.index("tool_invocation_requested") < event_types.index(
        "tool_policy_allowed"
    )
    assert event_types.index("tool_policy_allowed") < event_types.index(
        "tool_invocation_started"
    )
    assert event_types.index("tool_invocation_started") < event_types.index(
        "tool_succeeded"
    )


def test_dispatcher_replays_idempotent_mutation_without_duplicate_side_effect(
    tmp_path: Path,
) -> None:
    dispatcher, store = _runtime(tmp_path)
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.create",
        {"path": "module.py", "content": "stable\n"},
        idempotency_key="same-operation",
    )
    first = dispatcher.dispatch(invocation)

    replay = dispatcher.dispatch(
        invocation.model_copy(update={"invocation_id": "inv-retry"})
    )

    assert first.result is not None
    assert replay.replayed is True
    assert replay.record.invocation_id == invocation.invocation_id
    assert replay.result == replay.record.safe_result
    assert (tmp_path / "module.py").read_text(encoding="utf-8") == "stable\n"
    assert len(store.list_tool_invocations("run-1")) == 1
    assert len(store.list_tool_audits("run-1")) == 1
    assert dispatcher.budget_ledger.snapshot("run-1").invocation_count == 1


def test_dispatcher_persists_policy_denial_without_executing_tool(
    tmp_path: Path,
) -> None:
    dispatcher, store = _runtime(tmp_path)
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.read",
        {"path": "../outside.txt"},
    )

    response = dispatcher.dispatch(invocation)

    assert response.result is not None
    assert response.result.status == ToolInvocationStatus.DENIED
    assert response.result.policy_decision.rule_id == "deny.unsafe_path_syntax"
    assert response.record.started_at is None
    assert response.audit is not None
    assert len(store.list_tool_audits("run-1")) == 1


def test_dispatcher_suspends_and_resumes_exact_delete_approval(
    tmp_path: Path,
) -> None:
    target = tmp_path / "obsolete.py"
    target.write_text("obsolete\n", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    dispatcher, store = _runtime(tmp_path)
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.delete",
        {"path": "obsolete.py", "expected_sha256": digest},
    )

    pending = dispatcher.dispatch(invocation)

    assert pending.awaiting_approval is True
    assert pending.record.status == ToolInvocationStatus.AWAITING_APPROVAL
    assert target.exists()
    request = pending.approval_request
    grant = decide_tool_approval(
        request,
        invocation,
        disposition=ToolApprovalDisposition.APPROVED,
        reason="Delete obsolete source",
    )

    completed = dispatcher.dispatch(invocation, approval=grant)

    assert completed.result is not None
    assert completed.result.status == ToolInvocationStatus.SUCCEEDED
    assert completed.result.approval_id == request.approval_id
    assert target.exists() is False
    approval = store.get_tool_approval("run-1", request.approval_id)
    assert approval.disposition == ToolApprovalDisposition.APPROVED.value


def test_dispatcher_cancels_between_policy_and_execution_without_side_effect(
    tmp_path: Path,
) -> None:
    token = CancellationToken()
    policy = _CancellingPolicyEngine(token)
    dispatcher, store = _runtime(tmp_path, policy_engine=policy)
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.create",
        {"path": "never-created.py", "content": "unsafe\n"},
    )

    response = dispatcher.dispatch(invocation, cancellation=token)

    assert response.result is not None
    assert response.result.status == ToolInvocationStatus.CANCELLED
    assert response.result.cancellation.signal_sent is False
    assert response.result.cancellation.process_terminated is False
    assert response.result.cancellation.cleanup_completed is True
    assert (tmp_path / "never-created.py").exists() is False
    assert dispatcher.budget_ledger.snapshot("run-1").active_invocations == ()
    event_types = [event["event_type"] for event in store.list_events("run-1")]
    assert event_types.index("tool_cancel_requested") < event_types.index(
        "tool_cancelled"
    )
    assert event_types.index("tool_cancelled") < event_types.index(
        "tool_cleanup_completed"
    )


def test_completed_idempotent_result_replays_after_run_cancellation(
    tmp_path: Path,
) -> None:
    dispatcher, _ = _runtime(tmp_path)
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.create",
        {"path": "completed.py", "content": "complete\n"},
        idempotency_key="completed-before-cancel",
    )
    dispatcher.dispatch(invocation)
    cancellation = CancellationToken()
    cancellation.request("cancel remaining work")

    replay = dispatcher.dispatch(invocation, cancellation=cancellation)

    assert replay.replayed is True
    assert replay.record.status == ToolInvocationStatus.SUCCEEDED


def test_new_invocation_is_rejected_before_policy_after_cancellation(
    tmp_path: Path,
) -> None:
    dispatcher, store = _runtime(tmp_path)
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.create",
        {"path": "blocked.py", "content": "blocked\n"},
    )
    cancellation = CancellationToken()
    cancellation.request("stop run")

    with pytest.raises(CancellationRequested):
        dispatcher.dispatch(invocation, cancellation=cancellation)

    assert store.list_tool_invocations("run-1") == []
    assert (tmp_path / "blocked.py").exists() is False


def test_dispatcher_streams_and_cancels_real_process_tree(tmp_path: Path) -> None:
    output_observed = Event()
    dispatcher, store = _runtime(tmp_path, output_observed=output_observed)
    cancellation = CancellationRegistry(store).get("run-1")
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "process.execute",
        {
            "executable": "python",
            "arguments": [
                "-c",
                "import time; print('ready', flush=True); time.sleep(30)",
            ],
        },
        budget=ToolResourceBudget(wall_clock_seconds=10),
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            dispatcher.dispatch,
            invocation,
            cancellation=cancellation,
        )
        operation = cancellation.wait_for_active_operation(
            source="sandbox-process",
            timeout_seconds=5,
        )
        assert operation is not None
        assert output_observed.wait(timeout=5)
        cancellation.request("stop managed process")
        response = future.result(timeout=10)

    assert response.result is not None
    assert response.result.status == ToolInvocationStatus.CANCELLED
    assert response.result.cancellation.signal_sent is True
    assert response.result.cancellation.acknowledged is True
    assert response.result.cancellation.process_terminated is True
    event_types = [event["event_type"] for event in store.list_events("run-1")]
    assert "tool_output_chunk" in event_types
    assert event_types.index("tool_cancel_acknowledged") < event_types.index(
        "tool_cancelled"
    )


def test_dispatcher_classifies_nonzero_timeout_and_excessive_output(
    tmp_path: Path,
) -> None:
    dispatcher, store = _runtime(tmp_path)
    failed = dispatcher.dispatch(
        _invocation(
            dispatcher,
            tmp_path,
            "process.execute",
            {
                "executable": "python",
                "arguments": ["-c", "raise SystemExit(7)"],
            },
            invocation_id="inv-failed",
        )
    ).result
    timed_out = dispatcher.dispatch(
        _invocation(
            dispatcher,
            tmp_path,
            "process.execute",
            {
                "executable": "python",
                "arguments": ["-c", "import time; time.sleep(5)"],
            },
            invocation_id="inv-timeout",
            budget=ToolResourceBudget(wall_clock_seconds=0.1),
        )
    ).result
    excessive = dispatcher.dispatch(
        _invocation(
            dispatcher,
            tmp_path,
            "process.execute",
            {
                "executable": "python",
                "arguments": ["-c", "print('x' * 100)"],
            },
            invocation_id="inv-output",
            budget=ToolResourceBudget(
                stdout_bytes=8,
                stderr_bytes=8,
                combined_output_bytes=16,
            ),
        )
    ).result

    assert failed.status == ToolInvocationStatus.FAILED
    assert failed.error.category == ToolErrorCategory.PROCESS
    assert failed.exit_code == 7
    assert timed_out.status == ToolInvocationStatus.TIMED_OUT
    assert timed_out.error.category == ToolErrorCategory.TIMED_OUT
    assert timed_out.timed_out is True
    assert excessive.status == ToolInvocationStatus.FAILED
    assert excessive.error.category == ToolErrorCategory.RESOURCE_EXHAUSTED
    assert excessive.stdout_truncated is True
    assert len(excessive.stdout.encode("utf-8")) <= 8
    assert len(store.list_tool_audits("run-1")) == 3


def test_dispatcher_rejects_capability_mismatch_before_persistence(
    tmp_path: Path,
) -> None:
    dispatcher, store = _runtime(tmp_path)
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.read",
        {"path": "module.py"},
    )
    descriptor = dispatcher.registry.descriptor("filesystem.read")

    with pytest.raises(ValueError, match="exactly match"):
        dispatcher.dispatch(
            invocation.model_copy(
                update={"requested_capabilities": descriptor.capabilities}
            )
        )

    assert store.list_tool_invocations("run-1") == []


def test_dispatcher_reports_budget_rejection_before_second_mutation(
    tmp_path: Path,
) -> None:
    dispatcher, store = _runtime(tmp_path)
    budget = ToolResourceBudget(total_written_bytes=5)
    dispatcher.dispatch(
        _invocation(
            dispatcher,
            tmp_path,
            "filesystem.create",
            {"path": "first.txt", "content": "first"},
            invocation_id="inv-first",
            budget=budget,
        )
    )
    second = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.create",
        {"path": "second.txt", "content": "x"},
        invocation_id="inv-second",
        budget=budget,
    )

    with pytest.raises(ToolBudgetExceeded, match="reservation exceeds"):
        dispatcher.dispatch(second)

    assert (tmp_path / "second.txt").exists() is False
    assert len(store.list_tool_invocations("run-1")) == 1
    rejected = [
        event
        for event in store.list_events("run-1")
        if event["event_type"] == "tool_budget_rejected"
    ]
    assert rejected[0]["payload"]["limit_name"] == "total_written_bytes"


def test_rejected_approval_denies_without_deleting_file(tmp_path: Path) -> None:
    target = tmp_path / "keep.py"
    target.write_text("keep\n", encoding="utf-8")
    dispatcher, _ = _runtime(tmp_path)
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.delete",
        {
            "path": "keep.py",
            "expected_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        },
    )
    pending = dispatcher.dispatch(invocation)
    grant = decide_tool_approval(
        pending.approval_request,
        invocation,
        disposition=ToolApprovalDisposition.REJECTED,
        reason="Keep this file",
    )

    denied = dispatcher.dispatch(invocation, approval=grant)

    assert denied.result.status == ToolInvocationStatus.DENIED
    assert denied.result.error.category == ToolErrorCategory.APPROVAL_INVALID
    assert target.exists() is True


def test_recover_run_terminalizes_and_audits_interrupted_invocation(
    tmp_path: Path,
) -> None:
    dispatcher, store = _runtime(tmp_path)
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.read",
        {"path": "module.py"},
    )
    descriptor = dispatcher.registry.descriptor(invocation.tool_name)
    anticipated = anticipated_tool_usage(invocation)
    store.record_tool_invocation(
        invocation,
        anticipated_usage=anticipated,
        process_slot=requires_process_slot(invocation),
    )
    decision = dispatcher.policy_engine.evaluate(invocation, descriptor)
    store.record_tool_policy_decision("run-1", decision)
    store.mark_tool_invocation_started("run-1", invocation.invocation_id)

    reconciled = dispatcher.recover_run("run-1")

    assert reconciled[0].status == ToolInvocationStatus.FAILED
    assert reconciled[0].safe_result.error.retryable is False
    assert len(store.list_tool_audits("run-1")) == 1
    assert dispatcher.budget_ledger.snapshot("run-1").active_invocations == ()
    replay = dispatcher.dispatch(invocation)
    assert replay.replayed is True
    assert replay.record.status == ToolInvocationStatus.FAILED


def test_recover_run_repairs_missing_restart_audit_without_rerun(
    tmp_path: Path,
) -> None:
    dispatcher, store = _runtime(tmp_path)
    invocation = _invocation(
        dispatcher,
        tmp_path,
        "filesystem.read",
        {"path": "module.py"},
    )
    descriptor = dispatcher.registry.descriptor(invocation.tool_name)
    store.record_tool_invocation(invocation)
    store.record_tool_policy_decision(
        "run-1",
        dispatcher.policy_engine.evaluate(invocation, descriptor),
    )
    store.mark_tool_invocation_started("run-1", invocation.invocation_id)
    store.reconcile_running_tool_invocations("run-1")
    assert store.list_tool_audits("run-1") == []

    reconciled = dispatcher.recover_run("run-1")

    assert reconciled == ()
    assert len(store.list_tool_audits("run-1")) == 1


def _runtime(
    root: Path,
    *,
    policy_engine: ToolPolicyEngine | None = None,
    output_observed: Event | None = None,
) -> tuple[ToolDispatcher, StateStore]:
    store = StateStore(root / "state.db")
    store.create_run_with_tasks(
        RunRecord(
            run_id="run-1",
            original_task="Managed tool workflow",
            model="fake",
            workspace=str(root.resolve()),
            planner_output={"goal": "test", "steps": []},
            graph_data={"version": 1, "tasks": []},
        ),
        [
            TaskSpec(
                task_id="task-1",
                title="Invoke managed tool",
                description="Exercise dispatcher lifecycle",
            )
        ],
    )
    dispatcher_holder = {}

    def record_output(invocation, chunk) -> None:
        dispatcher_holder["dispatcher"].record_output_chunk(invocation, chunk)
        if output_observed is not None:
            output_observed.set()

    registry = builtin_tool_registry(
        workspace=root,
        executable_catalog=ExecutableCatalog({"python": sys.executable}),
        output_callback=record_output,
    )
    dispatcher = ToolDispatcher(
        registry,
        store,
        policy_engine=policy_engine,
    )
    dispatcher_holder["dispatcher"] = dispatcher
    return dispatcher, store


def _invocation(
    dispatcher: ToolDispatcher,
    root: Path,
    tool_name: str,
    arguments: dict,
    *,
    invocation_id: str = "inv-1",
    idempotency_key: str | None = None,
    budget: ToolResourceBudget | None = None,
) -> ToolInvocation:
    descriptor = dispatcher.registry.descriptor(tool_name)
    provisional = ToolInvocation(
        invocation_id=invocation_id,
        run_id="run-1",
        task_id="task-1",
        tool_name=tool_name,
        tool_version=descriptor.version,
        arguments=arguments,
        requested_capabilities=descriptor.capabilities,
        context=ToolInvocationContext(
            workspace_identity=str(root.resolve()),
            worktree_identity=str(root.resolve()),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
        idempotency_key=idempotency_key,
        resource_budget=budget or ToolResourceBudget(),
    )
    required = derive_required_capabilities(provisional, descriptor)
    return ToolInvocation.model_validate(
        provisional.model_dump(mode="python")
        | {"requested_capabilities": required}
    )


class _CancellingPolicyEngine(ToolPolicyEngine):
    def __init__(self, cancellation: CancellationToken) -> None:
        super().__init__()
        self._cancellation = cancellation

    def evaluate(self, invocation, descriptor, *, approval=None):
        decision = super().evaluate(
            invocation,
            descriptor,
            approval=approval,
        )
        self._cancellation.request("cancel after policy")
        return decision
