from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.policy import ToolApprovalDisposition, decide_tool_approval
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools import builtin_tool_registry
from agentbus.tools.capabilities import derive_required_capabilities
from agentbus.tools.dispatcher import ToolDispatcher
from agentbus.tools.protocol import (
    ToolInvocation,
    ToolInvocationContext,
    ToolInvocationStatus,
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


def _runtime(root: Path) -> tuple[ToolDispatcher, StateStore]:
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
    registry = builtin_tool_registry(
        workspace=root,
        executable_catalog=ExecutableCatalog({"python": sys.executable}),
    )
    return ToolDispatcher(registry, store), store


def _invocation(
    dispatcher: ToolDispatcher,
    root: Path,
    tool_name: str,
    arguments: dict,
    *,
    idempotency_key: str | None = None,
) -> ToolInvocation:
    descriptor = dispatcher.registry.descriptor(tool_name)
    provisional = ToolInvocation(
        invocation_id="inv-1",
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
    )
    required = derive_required_capabilities(provisional, descriptor)
    return ToolInvocation.model_validate(
        provisional.model_dump(mode="python")
        | {"requested_capabilities": required}
    )
