from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from agentbus.config import AgentBusConfig
from agentbus.control.app import ControlAppContext, create_app
from agentbus.control.models import (
    CancelResponse,
    ResumeResponse,
    RunAcceptedResponse,
)
from agentbus.control.services import ControlQueryService
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore

TOKEN = "test-control-token-that-is-at-least-thirty-two-bytes"


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
        )

    def shutdown(self, *, wait=True):
        return None


def _client(tmp_path: Path) -> tuple[TestClient, StubSupervisor]:
    store = StateStore(tmp_path / "state.db")
    store.create_run(
        RunRecord(
            run_id="run-1",
            original_task="Inspect me",
            workflow_type="multi",
            model="fake",
            workspace=str(tmp_path),
        )
    )
    config = AgentBusConfig(
        workspace_dir=str(tmp_path),
        state_db=str(tmp_path / "state.db"),
    )
    supervisor = StubSupervisor()
    app = create_app(
        token=TOKEN,
        query_service=ControlQueryService(config, store),
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
