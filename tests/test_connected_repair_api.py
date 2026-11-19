from datetime import timedelta

import pytest

from test_control_app import _auth, _client

from agentbus.repair.live.contracts import RepairPolicy, now

ROOT = "/api/v1/customer-repair"


@pytest.mark.parametrize("method,path", [
    ("get", "/connections"), ("post", "/connections/target/check"),
    ("post", "/plans/" + "a" * 32 + "/approve"), ("post", "/plans/" + "a" * 32 + "/execute"),
    ("post", "/plans/" + "a" * 32 + "/reconcile"), ("post", "/plans/" + "a" * 32 + "/stop"),
])
def test_connected_routes_share_existing_auth_and_origin_guards(tmp_path, method, path):
    client, _ = _client(tmp_path)
    assert getattr(client, method)(ROOT + path).status_code in {401, 403}
    assert getattr(client, method)(ROOT + path, headers={**_auth(), "Origin": "https://attacker.invalid"}).status_code == 403


def test_browser_cannot_register_destinations_or_credentials(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get(ROOT + "/connections", headers=_auth()).json() == {"connections": []}
    response = client.post(ROOT + "/connections", headers=_auth(), json={"destination": "http://169.254.169.254", "token": "never-echo-this"})
    assert response.status_code == 405
    assert "never-echo-this" not in response.text


def test_connected_validation_never_echoes_secrets_and_requires_strict_trust(tmp_path):
    client, _ = _client(tmp_path)
    response = client.post(ROOT + "/cases/" + "a" * 32 + "/plans", headers=_auth(), json={
        "scan_id": "b" * 32, "purchase_ids": ["untrusted-credential"], "workspace_trusted": "true", "secret": "never-echo-this"})
    assert response.status_code == 422
    assert "never-echo-this" not in response.text


def test_guided_demo_cannot_silently_become_a_production_target(tmp_path):
    client, _ = _client(tmp_path)
    case_id = client.post(ROOT + "/demo", headers=_auth()).json()["case"]["summary"]["case_id"]
    at = now()
    policy = RepairPolicy(incident_start=at - timedelta(days=2), incident_end=at)
    response = client.post(f"{ROOT}/cases/{case_id}/connected-preview", headers=_auth(), json={"connection_id": "unknown", "policy": policy.model_dump(mode="json")})
    assert response.status_code == 409
    assert "guided demo" in response.json()["error"]["message"]
