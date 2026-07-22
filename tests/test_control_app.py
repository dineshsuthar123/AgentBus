from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from agentbus.config import AgentBusConfig
from agentbus.control.app import ControlAppContext, create_app
from agentbus.control.models import (
    CancellationLifecycle,
    CancelResponse,
    ResumeResponse,
    RunAcceptedResponse,
)
from agentbus.control.services import ControlQueryService
from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.mcp import McpServerConfig, mcp_server_capabilities
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools.protocol import ToolCapabilityName
from agentbus.tools.runtime import build_managed_tool_runtime

TOKEN = "test-control-token-that-is-at-least-thirty-two-bytes"
MCP_FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "fake_server.py"


class StubSupervisor:
    def __init__(self) -> None:
        self.submissions = []
        self.cancelled = []
        self.resumed = []

    def submit(self, request):
        self.submissions.append(request)
        return RunAcceptedResponse(
            run_id="accepted-run",
            status="pending",
            workspace=request.workspace,
            created_at=datetime.now(timezone.utc),
        )

    def resume(self, run_id):
        self.resumed.append(run_id)
        return ResumeResponse(run_id=run_id, status="running", resumed=True)

    def cancel(self, run_id, reason=None):
        self.cancelled.append((run_id, reason))
        return CancelResponse(
            run_id=run_id,
            status="cancelled",
            cancellation_requested=True,
            cancellation=CancellationLifecycle(
                requested=True,
                reason=reason,
                revision=1,
            ),
        )

    def shutdown(self, *, wait=True):
        return None


def _client(
    tmp_path: Path,
    *,
    mcp_server_configs: tuple[McpServerConfig, ...] = (),
    mcp_executable_catalog: ExecutableCatalog | None = None,
) -> tuple[TestClient, StubSupervisor]:
    store = StateStore(tmp_path / "state.db")
    store.create_run_with_tasks(
        RunRecord(
            run_id="run-1",
            original_task="Inspect me",
            workflow_type="multi",
            model="fake",
            workspace=str(tmp_path),
        ),
        [
            TaskSpec(
                task_id="task-1",
                title="Inspect tools",
                description="Exercise control tool APIs",
            )
        ],
    )
    config = AgentBusConfig(
        workspace_dir=str(tmp_path),
        state_db=str(tmp_path / "state.db"),
        mcp_server_configs=mcp_server_configs,
    )
    supervisor = StubSupervisor()
    app = create_app(
        token=TOKEN,
        query_service=ControlQueryService(
            config,
            store,
            mcp_executable_catalog=mcp_executable_catalog,
        ),
        supervisor=supervisor,
        context=ControlAppContext(
            daemon_id="daemon-1",
            host="127.0.0.1",
            port=43123,
            started_at=datetime.now(timezone.utc),
            state_database=str(tmp_path / "state.db"),
        ),
        shutdown_supervisor=False,
    )
    return TestClient(app, raise_server_exceptions=False), supervisor


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_health_is_minimal_and_unauthenticated(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "protocol_version": "1.0"}
    assert TOKEN not in response.text


def test_api_requires_valid_bearer_header(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    missing = client.get("/api/v1/info")
    invalid = client.get(
        "/api/v1/info",
        headers={"Authorization": "Bearer incorrect-token"},
    )
    valid = client.get("/api/v1/info", headers=_auth())

    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert valid.status_code == 200
    assert missing.json()["error"]["code"] == "forbidden"
    assert TOKEN not in missing.text + invalid.text + valid.text


def test_untrusted_origin_is_rejected_and_cors_is_not_enabled(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    rejected = client.get(
        "/api/v1/info",
        headers={**_auth(), "Origin": "https://example.com"},
    )
    accepted = client.get("/api/v1/info", headers=_auth())

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert "access-control-allow-origin" not in accepted.headers


def test_validation_errors_use_stable_error_envelope(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.post(
        "/api/v1/runs",
        headers=_auth(),
        json={"task": "", "workspace": str(tmp_path), "unknown": "value"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "traceback" not in response.text.lower()


def test_request_body_limit_is_enforced_before_routing(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.post(
        "/api/v1/runs",
        headers={
            **_auth(),
            "Content-Length": "1000001",
            "Content-Type": "application/json",
        },
        content=b"{}",
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_run_list_and_inspection_return_transport_models(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    listed = client.get("/api/v1/runs", headers=_auth())
    inspected = client.get("/api/v1/runs/run-1", headers=_auth())

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert inspected.json()["run_id"] == "run-1"
    assert "planner_output_json" not in inspected.text


def test_run_submission_returns_accepted_id(tmp_path: Path) -> None:
    client, supervisor = _client(tmp_path)

    response = client.post(
        "/api/v1/runs",
        headers=_auth(),
        json={
            "task": "Implement safely",
            "workspace": str(tmp_path),
            "workflow": "single",
        },
    )

    assert response.status_code == 202
    assert response.json()["run_id"] == "accepted-run"
    assert len(supervisor.submissions) == 1


def test_cancel_and_resume_delegate_without_exposing_internal_state(
    tmp_path: Path,
) -> None:
    client, supervisor = _client(tmp_path)

    cancelled = client.post(
        "/api/v1/runs/run-1/cancel",
        headers=_auth(),
        json={"reason": "Stop locally"},
    )
    resumed = client.post("/api/v1/runs/run-1/resume", headers=_auth())

    assert cancelled.json()["status"] == "cancelled"
    assert resumed.json()["resumed"] is True
    assert supervisor.cancelled == [("run-1", "Stop locally")]
    assert supervisor.resumed == ["run-1"]


def test_openapi_uses_versioned_routes_and_stable_error_schema(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    schema = client.app.openapi()

    assert "/api/v1/runs" in schema["paths"]
    assert "/api/v1/events" in schema["paths"]
    assert "ErrorResponse" in schema["components"]["schemas"]


def test_tool_registry_and_policy_endpoints_are_bounded_and_diagnostic_only(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)

    listed = client.get("/api/v1/tools", headers=_auth())
    detail = client.get("/api/v1/tools/filesystem.read", headers=_auth())
    policy = client.get("/api/v1/policy", headers=_auth())
    evaluated = client.post(
        "/api/v1/policy/evaluate",
        headers=_auth(),
        json={
            "run_id": "run-1",
            "task_id": "task-1",
            "tool_name": "filesystem.read",
            "arguments": {"path": "README.md"},
            "expected_capabilities": ["filesystem.read"],
            "caller_role": "coder",
            "workspace_trusted": True,
            "provider_consented": True,
        },
    )

    assert listed.status_code == 200
    assert listed.json()["total"] >= 10
    assert detail.status_code == 200
    assert detail.json()["argument_schema"]["additionalProperties"] is False
    assert policy.status_code == 200
    rule_ids = {rule["rule_id"] for rule in policy.json()["rules"]}
    assert {
        "deny.protected_file",
        "deny.shell_execution",
        "approval.extended_process_budget",
        "allow.approved_invocation",
    } <= rule_ids
    assert evaluated.status_code == 200
    assert evaluated.json()["diagnostic_only"] is True
    assert evaluated.json()["persisted"] is False
    assert evaluated.json()["decision"]["outcome"] == "allow"
    assert (
        client.app.state.query_service.store.list_tool_invocations("run-1") == []
    )
    assert "post" not in client.app.openapi()["paths"]["/api/v1/tools"]


def test_policy_diagnostic_denies_secrets_and_rejects_capability_mismatch(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    common = {
        "run_id": "run-1",
        "task_id": "task-1",
        "tool_name": "filesystem.read",
        "caller_role": "coder",
        "workspace_trusted": True,
        "provider_consented": True,
    }

    denied = client.post(
        "/api/v1/policy/evaluate",
        headers=_auth(),
        json={
            **common,
            "arguments": {"path": ".env"},
            "expected_capabilities": ["filesystem.read"],
        },
    )
    mismatched = client.post(
        "/api/v1/policy/evaluate",
        headers=_auth(),
        json={
            **common,
            "arguments": {"path": "README.md"},
            "expected_capabilities": ["filesystem.write"],
        },
    )
    unknown_task = client.post(
        "/api/v1/policy/evaluate",
        headers=_auth(),
        json={
            **common,
            "task_id": "missing",
            "arguments": {"path": "README.md"},
        },
    )

    assert denied.status_code == 200
    assert denied.json()["decision"]["outcome"] == "deny"
    assert denied.json()["decision"]["rule_id"] == "deny.protected_file"
    assert mismatched.status_code == 403
    assert mismatched.json()["error"]["code"] == "forbidden"
    assert unknown_task.status_code == 404


def test_tool_invocation_and_audit_endpoints_expose_only_safe_replay_state(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    content = "control-tool-private-content\n"
    (tmp_path / "README.md").write_text(content, encoding="utf-8")
    runtime = build_managed_tool_runtime(
        workspace=tmp_path,
        state_store=client.app.state.query_service.store,
    )
    try:
        call = runtime.prepare_model_call(
            tool_name="filesystem.read",
            arguments={"path": "README.md"},
            expected_capabilities=(ToolCapabilityName.FILESYSTEM_READ,),
            run_id="run-1",
            task_id="task-1",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
            idempotency_key="control-safe-read",
        )
        response = runtime.invoke(
            call,
            run_id="run-1",
            task_id="task-1",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
            invocation_id="control-safe-read",
        )
    finally:
        runtime.close()

    listed = client.get(
        "/api/v1/runs/run-1/tool-invocations?limit=1",
        headers=_auth(),
    )
    detail = client.get(
        f"/api/v1/runs/run-1/tool-invocations/{response.invocation.invocation_id}",
        headers=_auth(),
    )
    audit = client.get("/api/v1/runs/run-1/tool-audit", headers=_auth())
    terminal_cancel = client.post(
        f"/api/v1/runs/run-1/tool-invocations/{response.invocation.invocation_id}/cancel",
        headers=_auth(),
    )
    unknown = client.get(
        "/api/v1/runs/run-1/tool-invocations/missing",
        headers=_auth(),
    )

    serialized = listed.text + detail.text + audit.text
    assert listed.status_code == 200
    assert listed.json()["invocations"][0]["status"] == "succeeded"
    assert detail.status_code == 200
    assert detail.json()["result"]["structured_output"]["persisted_summary"] is True
    assert audit.status_code == 200
    assert audit.json()["records"][0]["record"]["outcome"] == "succeeded"
    assert content.strip() not in serialized
    assert terminal_cancel.status_code == 409
    assert unknown.status_code == 404


def test_tool_approval_is_listed_decided_idempotently_and_cancellable(
    tmp_path: Path,
) -> None:
    client, supervisor = _client(tmp_path)
    content = b"delete only after exact control approval\n"
    target = tmp_path / "delete_me.txt"
    target.write_bytes(content)
    runtime = build_managed_tool_runtime(
        workspace=tmp_path,
        state_store=client.app.state.query_service.store,
    )
    try:
        call = runtime.prepare_model_call(
            tool_name="filesystem.delete",
            arguments={
                "path": "delete_me.txt",
                "expected_sha256": hashlib.sha256(content).hexdigest(),
            },
            expected_capabilities=(ToolCapabilityName.FILESYSTEM_DELETE,),
            run_id="run-1",
            task_id="task-1",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
            idempotency_key="control-delete",
        )
        pending = runtime.invoke(
            call,
            run_id="run-1",
            task_id="task-1",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
            invocation_id="control-delete",
        )
    finally:
        runtime.close()

    approval_id = pending.approval_request.approval_id
    listed = client.get("/api/v1/runs/run-1/approvals", headers=_auth())
    cancelled = client.post(
        "/api/v1/runs/run-1/tool-invocations/control-delete/cancel",
        headers=_auth(),
        json={"reason": "Stop the pending tool"},
    )
    approved = client.post(
        f"/api/v1/runs/run-1/approvals/{approval_id}/approve",
        headers=_auth(),
        json={"revision": 1, "reason": "Approve exact delete"},
    )
    repeated = client.post(
        f"/api/v1/runs/run-1/approvals/{approval_id}/approve",
        headers=_auth(),
        json={"revision": 1, "reason": "Ignored repeat reason"},
    )
    conflicting = client.post(
        f"/api/v1/runs/run-1/approvals/{approval_id}/reject",
        headers=_auth(),
        json={"revision": 1},
    )

    item = listed.json()["approvals"][0]
    assert listed.status_code == 200
    assert item["approval_kind"] == "tool"
    assert item["tool_name"] == "filesystem.delete"
    assert item["capabilities"][0]["name"] == "filesystem.delete"
    assert item["resource_budget"]["invocations_per_task"] == 64
    assert cancelled.status_code == 200
    assert cancelled.json()["run_cancellation_requested"] is True
    assert supervisor.cancelled == [("run-1", "Stop the pending tool")]
    assert approved.status_code == 200
    assert approved.json()["approval"]["state"] == "approved"
    assert repeated.json()["idempotent"] is True
    assert conflicting.status_code == 409
    assert target.exists()


def test_mcp_diagnostics_check_only_preconfigured_server_and_hide_command(
    tmp_path: Path,
) -> None:
    alias = "control-mcp"
    private_environment_value = "control-mcp-private-value"
    config = McpServerConfig(
        server_id="fixture",
        transport="stdio",
        executable_alias=alias,
        arguments=("--mode", "normal"),
        environment={"CI": private_environment_value},
        capability_map={
            "echo": mcp_server_capabilities("fixture"),
            "write_note": mcp_server_capabilities("fixture"),
        },
    )
    catalog = ExecutableCatalog(
        {alias: (sys.executable, "-u", str(MCP_FIXTURE))}
    )
    client, _ = _client(
        tmp_path,
        mcp_server_configs=(config,),
        mcp_executable_catalog=catalog,
    )

    listed = client.get("/api/v1/mcp/servers", headers=_auth())
    checked = client.post(
        "/api/v1/mcp/servers/fixture/check",
        headers=_auth(),
    )
    unknown = client.post(
        "/api/v1/mcp/servers/not-configured/check",
        headers=_auth(),
    )

    server = listed.json()["servers"][0]
    result = checked.json()
    serialized = listed.text + checked.text
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert server["server_id"] == "fixture"
    assert server["configured_tools"][0]["namespaced_name"] == "mcp.fixture.echo"
    assert "arguments" not in server
    assert "environment" not in server
    assert private_environment_value not in serialized
    assert str(MCP_FIXTURE) not in serialized
    assert checked.status_code == 200
    assert result["ready"] is True, result["message"]
    assert result["protocol_version"] == "2025-11-25"
    assert result["advertised_tools"] == [
        "mcp.fixture.echo",
        "mcp.fixture.write_note",
    ]
    assert result["cleanup_completed"] is True
    assert unknown.status_code == 404
    operation = client.app.openapi()["paths"][
        "/api/v1/mcp/servers/{server_id}/check"
    ]["post"]
    assert "requestBody" not in operation


def test_mcp_diagnostic_failure_is_bounded_and_cleans_transport(
    tmp_path: Path,
) -> None:
    alias = "control-mcp-unsupported"
    config = McpServerConfig(
        server_id="unsupported",
        transport="stdio",
        executable_alias=alias,
        arguments=("--mode", "unsupported"),
        capability_map={"echo": mcp_server_capabilities("unsupported")},
    )
    catalog = ExecutableCatalog(
        {alias: (sys.executable, "-u", str(MCP_FIXTURE))}
    )
    client, _ = _client(
        tmp_path,
        mcp_server_configs=(config,),
        mcp_executable_catalog=catalog,
    )

    checked = client.post(
        "/api/v1/mcp/servers/unsupported/check",
        headers=_auth(),
    )

    result = checked.json()
    assert checked.status_code == 200
    assert result["ready"] is False
    assert result["diagnostic_timeout_seconds"] == 10.0
    assert result["cleanup_completed"] is True
    assert "unsupported protocol version" in result["message"].lower()
