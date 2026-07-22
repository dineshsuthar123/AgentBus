from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentbus.execution.cancellation import CancellationRequested
from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools.capabilities import derive_required_capabilities
from agentbus.tools.protocol import StructuredToolCall, ToolInvocationStatus
from agentbus.tools.runtime import build_managed_tool_runtime


def test_structured_tool_call_rejects_malformed_and_capability_free_input() -> None:
    with pytest.raises(ValidationError, match="lowercase dotted"):
        StructuredToolCall(
            tool_name="Shell Command",
            arguments={},
            expected_capabilities=(),
        )
    with pytest.raises(ValidationError, match="expected capabilities"):
        StructuredToolCall(
            tool_name="filesystem.read",
            arguments={"path": "module.py"},
            expected_capabilities=(),
        )


def test_runtime_propagates_absolute_context_and_executes_structured_call(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    call = _call(
        runtime,
        tmp_path,
        "filesystem.create",
        {"path": "module.py", "content": "managed = True\n"},
    )

    response = runtime.invoke(
        call,
        run_id="run-1",
        task_id="task-1",
        caller_role="coder",
        workspace_trusted=True,
        provider_consented=True,
        invocation_id="inv-runtime",
    )

    root = str(tmp_path.resolve())
    assert response.result.status == ToolInvocationStatus.SUCCEEDED
    assert response.invocation.context.workspace_identity == root
    assert response.invocation.context.worktree_identity == root
    assert response.record.workspace_identity == root
    assert response.record.worktree_identity == root
    assert runtime.registry.descriptor(
        "filesystem.create"
    ).capabilities[0].scope.roots == (root,)
    assert (tmp_path / "module.py").read_text(encoding="utf-8") == (
        "managed = True\n"
    )
    assert len(store.list_tool_audits("run-1")) == 1


def test_runtime_keeps_reviewer_read_only_and_rejects_capability_mismatch(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    call = _call(
        runtime,
        tmp_path,
        "filesystem.create",
        {"path": "review.py", "content": "changed\n"},
    )

    denied = runtime.invoke(
        call,
        run_id="run-1",
        task_id="task-1",
        caller_role="reviewer",
        workspace_trusted=True,
        provider_consented=True,
        invocation_id="inv-reviewer",
    )

    assert denied.result.status == ToolInvocationStatus.DENIED
    assert denied.result.policy_decision.rule_id == "deny.reviewer_mutation"
    assert (tmp_path / "review.py").exists() is False

    descriptor = runtime.registry.descriptor("filesystem.read")
    mismatched = StructuredToolCall(
        tool_name="filesystem.read",
        arguments={"path": "module.py"},
        expected_capabilities=descriptor.capabilities,
    )
    with pytest.raises(ValueError, match="exactly match"):
        runtime.invoke(
            mismatched,
            run_id="run-1",
            task_id="task-1",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
            invocation_id="inv-mismatch",
        )
    assert len(store.list_tool_invocations("run-1")) == 1


def test_runtime_uses_shared_persisted_cancellation_token(tmp_path: Path) -> None:
    runtime, store = _runtime(tmp_path)
    call = _call(
        runtime,
        tmp_path,
        "filesystem.create",
        {"path": "blocked.py", "content": "blocked\n"},
    )
    runtime.cancellations.request("run-1", "operator stop")

    with pytest.raises(CancellationRequested):
        runtime.invoke(
            call,
            run_id="run-1",
            task_id="task-1",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        )

    assert store.get_cancellation_state("run-1").requested is True
    assert (tmp_path / "blocked.py").exists() is False


def test_runtime_rejects_invocation_context_outside_managed_roots(
    tmp_path: Path,
) -> None:
    runtime, store = _runtime(tmp_path)
    call = _call(
        runtime,
        tmp_path,
        "filesystem.create",
        {"path": "escape.py", "content": "blocked\n"},
    )
    invocation = runtime.invocation_from_call(
        call,
        run_id="run-1",
        task_id="task-1",
        caller_role="coder",
        workspace_trusted=True,
        provider_consented=True,
        invocation_id="inv-foreign-context",
    )
    foreign_context = invocation.context.model_copy(
        update={"workspace_identity": str(tmp_path.parent.resolve())}
    )

    with pytest.raises(ValueError, match="does not match"):
        runtime.dispatch(invocation.model_copy(update={"context": foreign_context}))

    assert store.list_tool_invocations("run-1") == []
    assert (tmp_path / "escape.py").exists() is False


def _runtime(root: Path):
    store = StateStore(root / "state.db")
    store.create_run_with_tasks(
        RunRecord(
            run_id="run-1",
            original_task="Structured tool call",
            model="fake",
            workspace=str(root.resolve()),
            planner_output={"goal": "test", "steps": []},
            graph_data={"version": 1, "tasks": []},
        ),
        [
            TaskSpec(
                task_id="task-1",
                title="Invoke structured tool",
                description="Exercise managed runtime",
            )
        ],
    )
    runtime = build_managed_tool_runtime(
        workspace=root,
        state_store=store,
        executable_catalog=ExecutableCatalog({"python": sys.executable}),
    )
    return runtime, store


def _call(runtime, root: Path, tool_name: str, arguments: dict):
    descriptor = runtime.registry.descriptor(tool_name)
    broad = StructuredToolCall(
        tool_name=tool_name,
        arguments=arguments,
        expected_capabilities=descriptor.capabilities,
    )
    provisional = runtime.invocation_from_call(
        broad,
        run_id="run-1",
        task_id="task-1",
        caller_role="coder",
        workspace_trusted=True,
        provider_consented=True,
        invocation_id="inv-provisional",
    )
    required = derive_required_capabilities(provisional, descriptor)
    return broad.model_copy(update={"expected_capabilities": required})
