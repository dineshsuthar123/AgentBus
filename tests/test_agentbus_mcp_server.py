from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from agentbus.config import AgentBusConfig
from agentbus.control.app import ControlAppContext, create_app
from agentbus.control.models import CancelResponse, RunAcceptedResponse
from agentbus.control.services import ControlQueryService
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore


TOKEN = "offline-agentbus-mcp-token-at-least-thirty-two-bytes"
PROTOCOL = "2025-11-25"


class _Supervisor:
    def __init__(self) -> None:
        self.submissions = []
        self.cancellations = []

    def submit(self, request):
        self.submissions.append(request)
        return RunAcceptedResponse(
            run_id="submitted-run",
            status="pending",
            workspace=request.workspace,
            created_at=datetime.now(timezone.utc),
        )

    def cancel(self, run_id, reason=None):
        self.cancellations.append((run_id, reason))
        return CancelResponse(
            run_id=run_id,
            status="cancelled",
            cancellation_requested=True,
        )

    def shutdown(self, *, wait=True):
        return None


def test_authenticated_mcp_lists_only_constrained_tools_and_inspects_run(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    _initialize(client)

    listed = _request(client, 2, "tools/list", {})
    names = {item["name"] for item in listed.json()["result"]["tools"]}
    inspected = _request(
        client,
        3,
        "tools/call",
        {"name": "agentbus.run.inspect", "arguments": {"run_id": "run-1"}},
    )

    assert names == {
        "agentbus.run.approvals",
        "agentbus.run.cancel",
        "agentbus.run.changes",
        "agentbus.run.diff",
        "agentbus.run.inspect",
        "agentbus.run.report",
        "agentbus.run.submit",
        "agentbus.run.tasks",
        "agentbus.tools.inspect",
    }
    assert "filesystem.read" not in names
    payload = inspected.json()["result"]["structuredContent"]
    assert payload["run_id"] == "run-1"
    assert "planner_output_json" not in inspected.text
    assert TOKEN not in inspected.text


def test_mcp_submission_forces_offline_managed_settings_and_cancel_is_bounded(
    tmp_path: Path,
) -> None:
    client, supervisor = _client(tmp_path)
    _initialize(client)

    submitted = _request(
        client,
        2,
        "tools/call",
        {
            "name": "agentbus.run.submit",
            "arguments": {
                "task": "Create a bounded offline file",
                "workspace": str(tmp_path),
            },
        },
    )
    cancelled = _request(
        client,
        3,
        "tools/call",
        {
            "name": "agentbus.run.cancel",
            "arguments": {"run_id": "run-1", "reason": "offline stop"},
        },
    )

    request = supervisor.submissions[0]
    assert submitted.json()["result"]["isError"] is False
    assert request.provider == "deterministic"
    assert request.live_provider_consent is False
    assert request.commit_changes is False
    assert request.create_pr is False
    assert request.durable is True
    assert supervisor.cancellations == [("run-1", "offline stop")]
    assert cancelled.json()["result"]["isError"] is False


def test_mcp_rejects_duplicate_batch_ids_before_mutation(tmp_path: Path) -> None:
    client, supervisor = _client(tmp_path)
    _initialize(client)
    call = {
        "jsonrpc": "2.0",
        "id": "duplicate-mutation",
        "method": "tools/call",
        "params": {
            "name": "agentbus.run.submit",
            "arguments": {
                "task": "Must execute at most once",
                "workspace": str(tmp_path),
            },
        },
    }

    response = client.post(
        "/mcp",
        headers={**_headers(), "MCP-Protocol-Version": PROTOCOL},
        json=[call, call],
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32600
    assert response.json()["id"] is None
    assert "duplicate" in response.json()["error"]["message"].lower()
    assert supervisor.submissions == []


def test_mcp_rejects_unauthenticated_unsupported_and_unexposed_requests(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    unauthorized = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    _initialize(client)
    unsupported = client.post(
        "/mcp",
        headers={**_headers(), "MCP-Protocol-Version": "2099-01-01"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    hidden = _request(
        client,
        3,
        "tools/call",
        {"name": "filesystem.read", "arguments": {"path": ".env"}},
    )

    assert unauthorized.status_code == 403
    assert unsupported.json()["error"]["code"] == -32000
    assert hidden.json()["result"]["isError"] is True
    assert ".env" not in hidden.text
    assert TOKEN not in unauthorized.text + unsupported.text + hidden.text


def test_mcp_requires_initialized_lifecycle_and_negotiated_http_header(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    initialize = client.post(
        "/mcp",
        headers=_headers(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "offline-test", "version": "1"},
            },
        },
    )
    before_ready = client.post(
        "/mcp",
        headers={**_headers(), "MCP-Protocol-Version": PROTOCOL},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    notification = client.post(
        "/mcp",
        headers={**_headers(), "MCP-Protocol-Version": PROTOCOL},
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    missing_header = client.post(
        "/mcp",
        headers=_headers(),
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
    )

    assert initialize.status_code == 200
    assert before_ready.json()["error"]["code"] == -32000
    assert notification.status_code == 202
    assert missing_header.json()["error"]["code"] == -32000


def _initialize(client: TestClient) -> None:
    initialized = client.post(
        "/mcp",
        headers=_headers(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "offline-test", "version": "1"},
            },
        },
    )
    assert initialized.status_code == 200
    assert initialized.json()["result"]["protocolVersion"] == PROTOCOL
    notification = client.post(
        "/mcp",
        headers={**_headers(), "MCP-Protocol-Version": PROTOCOL},
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert notification.status_code == 202


def _request(
    client: TestClient,
    request_id: int,
    method: str,
    params: dict,
):
    response = client.post(
        "/mcp",
        headers={**_headers(), "MCP-Protocol-Version": PROTOCOL},
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        },
    )
    assert response.status_code == 200
    return response


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _client(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    store.create_run(
        RunRecord(
            run_id="run-1",
            original_task="Inspect through constrained MCP",
            workflow_type="multi",
            model="fake",
            workspace=str(tmp_path),
        )
    )
    config = AgentBusConfig(
        workspace_dir=str(tmp_path),
        state_db=str(tmp_path / "state.db"),
    )
    supervisor = _Supervisor()
    app = create_app(
        token=TOKEN,
        query_service=ControlQueryService(config, store),
        supervisor=supervisor,
        context=ControlAppContext(
            daemon_id="mcp-daemon",
            host="127.0.0.1",
            port=43123,
            started_at=datetime.now(timezone.utc),
            state_database=str(tmp_path / "state.db"),
        ),
        shutdown_supervisor=False,
    )
    return TestClient(app, raise_server_exceptions=False), supervisor
