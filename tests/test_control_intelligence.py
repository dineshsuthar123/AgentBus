from __future__ import annotations

import json
import subprocess
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
from agentbus.control.protocol import build_json_schema, build_openapi
from agentbus.control.services import ControlQueryService
from agentbus.intelligence.models import (
    IndexOperationKind,
    IndexOperationState,
)
from agentbus.intelligence.operations import IndexOperationLease


TOKEN = "repository-intelligence-control-token-with-thirty-two-bytes"


class _Supervisor:
    def submit(self, request):
        return RunAcceptedResponse(
            run_id="unused",
            status="pending",
            workspace=request.workspace,
            created_at=datetime.now(timezone.utc),
        )

    def resume(self, run_id):
        return ResumeResponse(run_id=run_id, status="running", resumed=True)

    def cancel(self, run_id, reason=None):
        return CancelResponse(
            run_id=run_id,
            status="cancelled",
            cancellation_requested=True,
            cancellation=CancellationLifecycle(requested=True, reason=reason),
        )

    def shutdown(self, *, wait=True):
        return None


def _repository(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, shell=False)
    (path / "pyproject.toml").write_text(
        "[project]\nname = \"control-intelligence\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    (path / "calculator.py").write_text(
        "def add(left: int, right: int) -> int:\n"
        "    \"\"\"control-private-source-marker\"\"\"\n"
        "    return left + right\n\n"
        "def calculate() -> int:\n"
        "    return add(1, 2)\n",
        encoding="utf-8",
    )
    (path / "test_calculator.py").write_text(
        "from calculator import add\n\n"
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    return path


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    repository = _repository(tmp_path / "repository")
    config = AgentBusConfig(
        workspace_dir=str(repository),
        state_db=str(tmp_path / "state" / "state.db"),
    )
    app = create_app(
        token=TOKEN,
        query_service=ControlQueryService(config),
        supervisor=_Supervisor(),
        context=ControlAppContext(
            daemon_id="repository-intelligence-test",
            host="127.0.0.1",
            port=0,
            started_at=datetime.now(timezone.utc),
            state_database=str(config.state_database_path),
        ),
        shutdown_supervisor=False,
    )
    return TestClient(app, raise_server_exceptions=False), repository


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _build(client: TestClient, repository: Path) -> dict:
    response = client.post(
        "/api/v1/workspaces/index",
        headers=_auth(),
        json={"workspace": str(repository), "workspace_trusted": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_index_lifecycle_is_authenticated_trust_aware_and_contained(
    tmp_path: Path,
) -> None:
    client, repository = _client(tmp_path)

    unauthenticated = client.post(
        "/api/v1/workspaces/index",
        json={"workspace": str(repository), "workspace_trusted": True},
    )
    untrusted = client.post(
        "/api/v1/workspaces/index",
        headers=_auth(),
        json={"workspace": str(repository), "workspace_trusted": False},
    )
    built = _build(client, repository)
    workspace_id = built["workspace_id"]
    status = client.get(
        f"/api/v1/workspaces/{workspace_id}/index",
        headers=_auth(),
    )
    verified = client.post(
        f"/api/v1/workspaces/{workspace_id}/index/verify",
        headers=_auth(),
    )
    info = client.get("/api/v1/info", headers=_auth())

    assert unauthenticated.status_code == 403
    assert untrusted.status_code == 403
    assert built["result"]["status"]["state"] == "current"
    assert built["result"]["provider_calls"] == 0
    assert built["result"]["network_calls"] == 0
    assert status.status_code == 200
    assert status.json()["status"]["state"] == "current"
    overview = status.json()["overview"]
    assert overview["projects"][0]["name"] == "control-intelligence"
    assert overview["languages"][0]["language"] == "python"
    assert overview["symbol_kind_counts"]["function"] == 2
    assert overview["symbol_kind_counts"]["test"] >= 1
    assert verified.status_code == 200
    assert verified.json()["result"]["valid"] is True
    assert "repository-intelligence" in info.json()["capabilities"]
    assert not (repository / "repository-index.sqlite3").exists()

    calculator = repository / "calculator.py"
    calculator.write_text(
        calculator.read_text(encoding="utf-8") + "\nVALUE = 3\n",
        encoding="utf-8",
    )
    untrusted_update = client.post(
        f"/api/v1/workspaces/{workspace_id}/index/update",
        headers=_auth(),
        json={"workspace_trusted": False},
    )
    updated = client.post(
        f"/api/v1/workspaces/{workspace_id}/index/update",
        headers=_auth(),
        json={"workspace_trusted": True},
    )
    assert untrusted_update.status_code == 403
    assert updated.status_code == 200
    assert updated.json()["result"]["status"]["state"] == "current"
    untrusted_repair = client.post(
        f"/api/v1/workspaces/{workspace_id}/index/repair",
        headers=_auth(),
        json={"workspace_trusted": False},
    )
    repaired = client.post(
        f"/api/v1/workspaces/{workspace_id}/index/repair",
        headers=_auth(),
        json={"workspace_trusted": True},
    )
    assert untrusted_repair.status_code == 403
    assert repaired.status_code == 200
    assert repaired.json()["result"]["operation"] == "repair"

    nested = repository / "nested"
    nested.mkdir()
    rejected = client.post(
        "/api/v1/workspaces/index",
        headers=_auth(),
        json={"workspace": str(nested), "workspace_trusted": True},
    )
    assert rejected.status_code == 403
    assert str(repository) not in rejected.text


def test_search_graph_impact_tests_and_context_are_bounded_and_source_free(
    tmp_path: Path,
) -> None:
    client, repository = _client(tmp_path)
    built = _build(client, repository)
    workspace_id = built["workspace_id"]

    search = client.post(
        f"/api/v1/workspaces/{workspace_id}/search",
        headers=_auth(),
        json={
            "query": "add",
            "projects": ["control-intelligence"],
            "languages": ["python"],
            "limit": 1,
        },
    )
    assert search.status_code == 200, search.text
    search_payload = search.json()["report"]
    assert len(search_payload["results"]) == 1
    add_symbol = next(
        item["symbol"]
        for item in search_payload["results"]
        if item["symbol"] is not None
    )
    assert add_symbol["signature"] is None

    calculate = client.post(
        f"/api/v1/workspaces/{workspace_id}/search",
        headers=_auth(),
        json={"query": "calculate", "limit": 10, "include_evidence": True},
    )
    calculate_symbol = next(
        item["symbol"]
        for item in calculate.json()["report"]["results"]
        if item["symbol"] is not None and item["symbol"]["name"] == "calculate"
    )
    symbol = client.get(
        (
            f"/api/v1/workspaces/{workspace_id}/symbols/"
            f"{calculate_symbol['symbol_id']}"
        ),
        headers=_auth(),
    )
    dependencies = client.get(
        (
            f"/api/v1/workspaces/{workspace_id}/dependencies/"
            f"{calculate_symbol['symbol_id']}?depth=2&limit=1"
        ),
        headers=_auth(),
    )
    dependents = client.get(
        (
            f"/api/v1/workspaces/{workspace_id}/dependents/"
            f"{add_symbol['symbol_id']}?depth=2&limit=1"
        ),
        headers=_auth(),
    )
    impact = client.post(
        f"/api/v1/workspaces/{workspace_id}/impact",
        headers=_auth(),
        json={"subjects": ["calculator.py"], "max_depth": 3},
    )
    tests = client.post(
        f"/api/v1/workspaces/{workspace_id}/tests",
        headers=_auth(),
        json={"subjects": ["calculator.py"], "max_depth": 3},
    )
    context = client.post(
        f"/api/v1/workspaces/{workspace_id}/context-plan",
        headers=_auth(),
        json={
            "task": "Change calculate safely",
            "role": "coder",
            "projects": ["control-intelligence"],
            "byte_budget": 20_000,
            "token_budget": 4_000,
        },
    )

    for response in (symbol, dependencies, dependents, impact, tests, context):
        assert response.status_code == 200, response.text
    assert symbol.json()["symbol"]["signature"] is None
    assert dependencies.json()["edges"]
    assert dependencies.json()["limit"] == 1
    assert dependents.json()["edges"]
    assert impact.json()["result"]["evidence"] == []
    assert "test_calculator.py" in tests.json()["result"]["selected_tests"]
    assert tests.json()["result"]["evidence"] == []
    assert any(
        item["selected"] for item in context.json()["result"]["candidates"]
    )
    assert all(
        item["reasons"] == []
        for item in context.json()["result"]["candidates"]
    )
    payload = "".join(
        response.text
        for response in (
            search,
            calculate,
            symbol,
            dependencies,
            dependents,
            impact,
            tests,
            context,
        )
    )
    assert "control-private-source-marker" not in payload
    assert '"content"' not in payload
    assert str(repository) not in payload


def test_index_operations_are_cancellable_and_duplicate_builds_are_fenced(
    tmp_path: Path,
) -> None:
    client, repository = _client(tmp_path)
    built = _build(client, repository)
    workspace_id = built["workspace_id"]
    manager = client.app.state.query_service.intelligence
    service = manager._services[workspace_id]
    lease = IndexOperationLease(
        service.store,
        service.repository,
        IndexOperationKind.BUILD,
    )
    lease.acquire()

    duplicate = client.post(
        "/api/v1/workspaces/index",
        headers=_auth(),
        json={"workspace": str(repository), "workspace_trusted": True},
    )
    cancelled = client.post(
        f"/api/v1/workspaces/{workspace_id}/index/cancel",
        headers=_auth(),
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"
    assert cancelled.status_code == 200
    assert cancelled.json()["cancellation_requested"] is True
    assert lease.checkpoint(force=True).cancellation_requested is True
    lease.finish(IndexOperationState.CANCELLED)


def test_intelligence_api_rejects_unbounded_and_unknown_queries(
    tmp_path: Path,
) -> None:
    client, repository = _client(tmp_path)
    workspace_id = _build(client, repository)["workspace_id"]

    oversized = client.post(
        f"/api/v1/workspaces/{workspace_id}/search",
        headers=_auth(),
        json={"query": "add", "limit": 201},
    )
    deep = client.get(
        f"/api/v1/workspaces/{workspace_id}/dependencies/symbol_missing?depth=9",
        headers=_auth(),
    )
    unknown = client.get(
        "/api/v1/workspaces/workspace_missing/index",
        headers=_auth(),
    )

    assert oversized.status_code == 422
    assert deep.status_code == 422
    assert unknown.status_code == 404
    assert "traceback" not in oversized.text + deep.text + unknown.text
    assert TOKEN not in oversized.text + deep.text + unknown.text


def test_repository_intelligence_routes_are_additive_protocol_v1() -> None:
    openapi = build_openapi()
    paths = openapi["paths"]
    expected = {
        "/api/v1/workspaces/index",
        "/api/v1/workspaces/{workspace_id}/index",
        "/api/v1/workspaces/{workspace_id}/index/update",
        "/api/v1/workspaces/{workspace_id}/index/verify",
        "/api/v1/workspaces/{workspace_id}/index/repair",
        "/api/v1/workspaces/{workspace_id}/index/cancel",
        "/api/v1/workspaces/{workspace_id}/search",
        "/api/v1/workspaces/{workspace_id}/symbols/{symbol_id}",
        "/api/v1/workspaces/{workspace_id}/dependencies/{symbol_id}",
        "/api/v1/workspaces/{workspace_id}/dependents/{symbol_id}",
        "/api/v1/workspaces/{workspace_id}/impact",
        "/api/v1/workspaces/{workspace_id}/tests",
        "/api/v1/workspaces/{workspace_id}/context-plan",
    }

    assert openapi["info"]["x-agentbus-protocol-version"] == "1.0"
    assert expected.issubset(paths)
    definitions = build_json_schema()["$defs"]
    assert "WorkspaceSearchRequest" in definitions
    assert "WorkspaceGraphResponse" in definitions
    assert "WorkspaceContextPlanResponse" in definitions
