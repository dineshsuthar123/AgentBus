from __future__ import annotations

import sys
from pathlib import Path

from agentbus.execution.cancellation import CancellationToken
from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.mcp import (
    McpServerConfig,
    McpStdioTransport,
    import_mcp_server,
    mcp_server_capabilities,
)
from agentbus.mcp.client import McpClient
from agentbus.policy import ToolApprovalDisposition, decide_tool_approval
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools.dispatcher import ToolDispatcher
from agentbus.tools.protocol import (
    StructuredToolCall,
    ToolInvocation,
    ToolInvocationContext,
    ToolErrorCategory,
    ToolInvocationStatus,
    ToolSafetyClassification,
)
from agentbus.tools.registry import ToolRegistry


FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "fake_server.py"


def test_importer_registers_namespaced_tools_without_trusting_annotations(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    config, client = _configured_client(tmp_path)

    with import_mcp_server(
        registry,
        config,
        worktree=tmp_path,
        client=client,
    ) as session:
        descriptors = session.descriptors
        resolved = registry.resolve("mcp.fixture.echo")

        assert [item.name for item in descriptors] == [
            "mcp.fixture.echo",
            "mcp.fixture.write_note",
        ]
        assert descriptors[0].safety == ToolSafetyClassification.SENSITIVE
        assert descriptors[0].idempotent is False
        assert descriptors[0].supports_cancellation is True
        assert resolved.descriptor == descriptors[0]

    assert client.is_connected is False


def test_imported_tool_passes_normal_policy_approval_and_audit(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    config, client = _configured_client(tmp_path)
    store = _store(tmp_path)
    token = CancellationToken()

    with import_mcp_server(
        registry,
        config,
        worktree=tmp_path,
        client=client,
    ):
        dispatcher = ToolDispatcher(registry, store)
        invocation = _invocation(registry, tmp_path)
        pending = dispatcher.dispatch(invocation, cancellation=token)

        assert pending.awaiting_approval is True
        assert pending.approval_request.policy_rule == "approval.mcp_invoke"
        grant = decide_tool_approval(
            pending.approval_request,
            invocation,
            disposition=ToolApprovalDisposition.APPROVED,
            reason="Invoke explicitly configured offline MCP fixture",
        )
        completed = dispatcher.dispatch(
            invocation,
            approval=grant,
            cancellation=token,
        )

    assert completed.result.status == ToolInvocationStatus.SUCCEEDED
    assert completed.result.structured_output["structured_content"] == {
        "echo": "managed hello"
    }
    assert completed.audit.record.capabilities == invocation.requested_capabilities
    assert completed.audit.record.policy_decision.rule_id == (
        "allow.approved_invocation"
    )
    assert completed.audit.record.approval_id == pending.approval_request.approval_id


def test_imported_remote_error_is_audited_as_mcp_failure(tmp_path: Path) -> None:
    registry = ToolRegistry()
    config, client = _configured_client(tmp_path, mode="tool-error")
    store = _store(tmp_path)
    token = CancellationToken()

    with import_mcp_server(
        registry,
        config,
        worktree=tmp_path,
        client=client,
    ):
        dispatcher = ToolDispatcher(registry, store)
        invocation = _invocation(registry, tmp_path)
        pending = dispatcher.dispatch(invocation, cancellation=token)
        grant = decide_tool_approval(
            pending.approval_request,
            invocation,
            disposition=ToolApprovalDisposition.APPROVED,
            reason="Exercise bounded MCP failure",
        )
        failed = dispatcher.dispatch(
            invocation,
            approval=grant,
            cancellation=token,
        )

    assert failed.result.status == ToolInvocationStatus.FAILED
    assert failed.result.error.category == ToolErrorCategory.MCP
    assert failed.result.error.code == "mcp_error"
    assert failed.audit.record.error_category == ToolErrorCategory.MCP


def _configured_client(root: Path, *, mode: str = "normal"):
    alias = "fake-mcp-import"
    catalog = ExecutableCatalog(
        {alias: (sys.executable, "-u", str(FIXTURE), "--mode", mode)}
    )
    config = McpServerConfig(
        server_id="fixture",
        transport="stdio",
        executable_alias=alias,
        capability_map={
            name: mcp_server_capabilities("fixture")
            for name in ("echo", "write_note")
        },
    )
    transport = McpStdioTransport(
        config,
        worktree=root,
        executable_catalog=catalog,
        shutdown_grace_seconds=0.2,
    )
    return config, McpClient(config, transport)


def _store(root: Path) -> StateStore:
    store = StateStore(root / "mcp-state.db")
    store.create_run_with_tasks(
        RunRecord(
            run_id="run-1",
            original_task="Invoke MCP",
            model="fake",
            workspace=str(root.resolve()),
            planner_output={"goal": "test", "steps": []},
            graph_data={"version": 1, "tasks": []},
        ),
        [TaskSpec(task_id="task-1", title="MCP", description="Invoke MCP")],
    )
    return store


def _invocation(registry: ToolRegistry, root: Path) -> ToolInvocation:
    descriptor = registry.descriptor("mcp.fixture.echo")
    call = StructuredToolCall(
        tool_name=descriptor.name,
        arguments={"message": "managed hello"},
        expected_capabilities=descriptor.capabilities,
    )
    return ToolInvocation(
        invocation_id="mcp-invocation-1",
        run_id="run-1",
        task_id="task-1",
        tool_name=call.tool_name,
        tool_version=descriptor.version,
        arguments=call.arguments,
        requested_capabilities=call.expected_capabilities,
        context=ToolInvocationContext(
            workspace_identity=str(root.resolve()),
            worktree_identity=str(root.resolve()),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
        timeout_seconds=5,
    )
