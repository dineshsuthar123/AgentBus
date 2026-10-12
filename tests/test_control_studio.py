from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest
from fastapi.testclient import TestClient

from agentbus.config import AgentBusConfig
from agentbus.control.app import ControlAppContext, create_app
from agentbus.control.errors import ControlPlaneForbiddenError
from agentbus.control.services import ControlQueryService
from agentbus.control.studio import BrowserSessions, launch_browser_exchange, loopback_origin
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepository

ORIGIN = "http://127.0.0.1:43123"
TOKEN = "studio-test-bearer-credential-at-least-32-characters"
BOOTSTRAP = "http://127.0.0.1:43124"


@pytest.fixture
def client(tmp_path, monkeypatch):
    assets = tmp_path / "package" / "studio_assets"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("<!doctype html><title>Studio</title>", encoding="utf-8")
    (assets / "app.js").write_text("// bounded packaged asset", encoding="utf-8")
    (tmp_path / "package" / "private.txt").write_text("not an asset", encoding="utf-8")
    monkeypatch.setattr("agentbus.control.studio.files", lambda _: assets.parent)
    store = StateStore(tmp_path / "state.db")
    store.create_run(RunRecord(run_id="run-1", original_task="Inspect Studio", workflow_type="multi", model="fake", workspace=str(tmp_path)))
    config = AgentBusConfig(workspace_dir=str(tmp_path), state_db=str(tmp_path / "state.db"))
    app = create_app(token=TOKEN, query_service=ControlQueryService(config, store), supervisor=object(),
                     context=ControlAppContext("studio-daemon", "127.0.0.1", 43123, datetime.now(timezone.utc), str(tmp_path / "state.db")),
                     shutdown_supervisor=False)
    return TestClient(app, base_url=ORIGIN, raise_server_exceptions=False)


def exchange(client):
    code = client.app.state.browser_sessions.ticket(BOOTSTRAP, "run-1")
    return client.post("/studio/session/exchange", data={"code": code}, headers={"Origin": BOOTSTRAP}, follow_redirects=False)


def browser_headers(**overrides):
    return {"Sec-Fetch-Site": "same-origin", "Origin": ORIGIN, **overrides}


def test_exchange_issues_private_cookie_without_url_credentials(client):
    response = exchange(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/studio/#/runs/run-1"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert TOKEN not in response.text and TOKEN not in cookie
    assert client.get("/api/v1/info", headers=browser_headers()).status_code == 200
    assert client.get("/api/v1/info").status_code == 403


@pytest.mark.parametrize("headers", [{"Origin": "http://localhost:43123", "Sec-Fetch-Site": "same-origin"},
                                     {"Origin": ORIGIN, "Sec-Fetch-Site": "same-site"},
                                     {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
                                     {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin", "Host": "127.0.0.1:1"}])
def test_browser_session_rejects_wrong_origin_site_and_host(client, headers):
    assert exchange(client).status_code == 303
    assert client.post("/studio/session/logout", headers=headers).status_code == 403
    assert client.get("/api/v1/info", headers=browser_headers()).status_code == 200


def test_logout_revokes_cookie_and_restart_rotates_sessions(client):
    exchange(client)
    old_cookie = client.cookies.get("syndra_session")
    assert client.post("/studio/session/logout", headers=browser_headers()).status_code == 204
    assert client.get("/api/v1/info", headers={**browser_headers(), "Cookie": f"syndra_session={old_cookie}"}).status_code == 403
    exchange(client)
    cookie = client.cookies.get("syndra_session")
    restarted = BrowserSessions(ORIGIN)
    with pytest.raises(ControlPlaneForbiddenError):
        restarted.authenticate(cookie, origin=ORIGIN, fetch_site="same-origin", mutation=True)


def test_ticket_is_single_use_bound_to_origin_and_daemon(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("agentbus.control.studio.time", type("Clock", (), {"monotonic": staticmethod(lambda: now[0])}))
    sessions = BrowserSessions(ORIGIN)
    code = sessions.ticket(BOOTSTRAP, "run-1")
    with pytest.raises(ControlPlaneForbiddenError):
        sessions.redeem(code, ORIGIN)
    with pytest.raises(ControlPlaneForbiddenError):
        BrowserSessions("http://127.0.0.1:43125").redeem(code, BOOTSTRAP)
    cookie, path = sessions.redeem(code, BOOTSTRAP)
    assert path == "/studio/#/runs/run-1"
    with pytest.raises(ControlPlaneForbiddenError):
        sessions.redeem(code, BOOTSTRAP)
    expired_code = sessions.ticket(BOOTSTRAP, None)
    now[0] += 31
    with pytest.raises(ControlPlaneForbiddenError):
        sessions.redeem(expired_code, BOOTSTRAP)
    sessions.authenticate(cookie, origin=ORIGIN, fetch_site="same-origin", mutation=True)
    now[0] += 7200
    with pytest.raises(ControlPlaneForbiddenError):
        sessions.authenticate(cookie, origin=ORIGIN, fetch_site="same-origin", mutation=False)


def test_exchange_rejects_reuse_wrong_host_and_malformed_body(client):
    code = client.app.state.browser_sessions.ticket(BOOTSTRAP, None)
    assert client.post("/studio/session/exchange", data={"code": code}, headers={"Origin": BOOTSTRAP, "Host": "evil.example"}).status_code == 403
    response = client.post("/studio/session/exchange", data={"code": code}, headers={"Origin": BOOTSTRAP}, follow_redirects=False)
    assert response.status_code == 303
    assert client.post("/studio/session/exchange", data={"code": code}, headers={"Origin": BOOTSTRAP}).status_code == 403
    for body in [b"\xff", b"broken", b"code=a&code=b", b"code=" + b"x" * 2048]:
        assert client.post("/studio/session/exchange", content=body, headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": BOOTSTRAP}).status_code == 400


def test_packaged_assets_are_public_bounded_and_do_not_swallow_api_errors(client):
    response = client.get("/studio/")
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/studio/app.js").status_code == 200
    assert client.get("/studio/%2e%2e/private.txt").status_code == 404
    assert client.get("/studio/missing.js").status_code == 404
    assert client.get("/api/v1/info").status_code == 403
    assert client.get("/api/v1/missing", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_launch_url_is_transient_credential_free_and_code_only_in_body(client):
    url = launch_browser_exchange(client.app.state.browser_sessions, "run-1")
    parsed = urlsplit(url)
    assert parsed.hostname == "127.0.0.1" and parsed.path == "/"
    assert not parsed.query and not parsed.fragment and not parsed.username
    with urlopen(url, timeout=5) as response:
        document = response.read().decode()
        assert response.headers["Cache-Control"] == "no-store"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert '<input type="hidden" name="code"' in document
    assert 'action="' + ORIGIN + '/studio/session/exchange"' in document
    assert TOKEN not in document


def test_ipv6_origin_and_non_loopback_rejection():
    assert loopback_origin("::1", 12345) == "http://[::1]:12345"
    assert BrowserSessions("http://[::1]:12345").origin == "http://[::1]:12345"
    with pytest.raises(ValueError):
        BrowserSessions("https://example.com:12345")


def test_guided_workspace_is_new_scoped_and_never_overwrites_user_work(client, tmp_path, monkeypatch):
    unrelated = tmp_path / "user-work.txt"
    unrelated.write_text("keep this work", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "unintended.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    assert client.post("/api/v1/studio/demo-workspaces", json={}).status_code == 403
    response = client.post("/api/v1/studio/demo-workspaces", json={"profile": "python-calculator"}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    result = response.json()
    workspace = Path(result["workspace"])
    assert workspace.parent == tmp_path / "demo-workspaces"
    assert (workspace / ".syndra-demo.json").is_file()
    assert (workspace / "calculator.py").is_file()
    assert unrelated.read_text(encoding="utf-8") == "keep this work"
    assert result["network_used"] is False
    assert result["execution_profile"] == "python-calculator-repair"
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    repository = GitRepository(workspace)
    assert repository.changed_files() == []
    rejected = client.post("/api/v1/studio/demo-workspaces", json={"profile": "python-calculator", "workspace": str(tmp_path)}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert rejected.status_code == 422
    again = client.post("/api/v1/studio/demo-workspaces", json={}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert again.status_code == 200 and again.json()["workspace"] != str(workspace)


def test_guided_repair_finishes_without_changing_tests_or_leaking_runtime_logs(client):
    from agentbus.control.models import RunCreateRequest
    from agentbus.control.supervisor import AgentBusRunBackend

    query = client.app.state.query_service
    fixture = query.create_demo_workspace("python-calculator")
    workspace = Path(fixture.workspace)
    original_test = (workspace / "test_calculator.py").read_bytes()
    request = RunCreateRequest(task=fixture.task, workspace=str(workspace), provider="deterministic",
                               workflow="multi", durable=True, deterministic={"profile": fixture.execution_profile})
    backend = AgentBusRunBackend(query.config, query.store)
    try:
        backend.prepare_submission(request, "guided-repair")
        backend.execute_new(request, "guided-repair")
        result = query.store.get_run("guided-repair")
        assert result.status == "succeeded", result.failure_reason
        assert result.verifier_status == "passed" and result.reviewer_status == "approved"
        assert (workspace / "test_calculator.py").read_bytes() == original_test
        assert GitRepository(workspace).changed_files() == ["calculator.py"]
        assert not (workspace / "runs").exists()
        assert not (workspace / ".syndra" / "runs").exists()
        assert list(query.config.runs_path.glob("*guided-repair.jsonl"))
        restored = backend._config_for_persisted_run(result)
        assert restored.runs_path == query.config.runs_path
        assert restored.state_database_path == query.config.state_database_path
        assert backend._config_for(request).runs_path == query.config.runs_path
    finally:
        backend.shutdown()
