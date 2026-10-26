from __future__ import annotations

import uuid

from test_control_app import _auth, _client

from agentbus.repair.demo import demo_snapshot

ROOT = "/api/v1/customer-repair"


def test_repair_routes_require_the_existing_authenticated_session(tmp_path):
    client, _ = _client(tmp_path)
    for method, path in (("get", "/cases"), ("get", "/template"), ("post", "/demo"), ("post", "/cases")):
        response = getattr(client, method)(ROOT + path)
        assert response.status_code in (401, 403)
        assert "snapshot" not in response.json()


def test_real_api_demo_and_saved_result_flow(tmp_path):
    client, _ = _client(tmp_path)
    case = client.post(ROOT + "/demo", headers=_auth()).json()["case"]
    case_id = case["summary"]["case_id"]
    assert case["demo_proof"]["historical_missing_after_fix"] == 65
    assert client.get(f"{ROOT}/cases/{case_id}/preview", headers=_auth()).json() == {"preview": None}
    preview = client.post(f"{ROOT}/cases/{case_id}/preview", headers=_auth()).json()["preview"]
    assert preview["counts"]["repairable"] == 65
    client.post(f"{ROOT}/cases/{case_id}/demo-change", headers=_auth(), json={"action": "support_corrects_five"}).raise_for_status()
    request_id = uuid.uuid4().hex
    body = dict(preview_id=preview["preview_id"], preview_fingerprint=preview["fingerprint"], request_id=request_id,
                purchase_ids=[row["purchase_id"] for row in preview["observations"] if row["disposition"] == "repairable"],
                confirmation="APPLY_SELECTED_CORRECTIONS")
    response = client.post(f"{ROOT}/cases/{case_id}/apply", headers=_auth(), json=body)
    assert response.status_code == 200
    assert response.json()["batch"]["restored_count"] == 60
    assert response.json()["batch"]["skipped_count"] == 5
    assert client.get(f"{ROOT}/cases/{case_id}/batches/{request_id}", headers=_auth()).json() == response.json()
    package = client.post(f"{ROOT}/cases/{case_id}/export", headers=_auth(), json={"preview_id": preview["preview_id"]})
    assert package.status_code == 200 and len(package.json()["package"]["batches"]) == 1
    events = client.get(f"{ROOT}/cases/{case_id}/events", headers=_auth()).json()["events"]
    assert events[0]["kind"] == "applied"
    assert client.get(ROOT + "/cases", headers=_auth()).json()["cases"][0]["batch_count"] == 1


def test_import_has_no_apply_or_demo_mutation_path(tmp_path):
    client, _ = _client(tmp_path)
    response = client.post(ROOT + "/cases", headers=_auth(), json={"snapshot": demo_snapshot().model_dump(mode="json")})
    assert response.status_code == 200
    case_id = response.json()["case"]["summary"]["case_id"]
    preview = client.post(f"{ROOT}/cases/{case_id}/preview", headers=_auth()).json()["preview"]
    result = client.post(f"{ROOT}/cases/{case_id}/apply", headers=_auth(), json={
        "preview_id": preview["preview_id"], "preview_fingerprint": preview["fingerprint"], "request_id": uuid.uuid4().hex,
        "purchase_ids": ["purchase-001"], "confirmation": "APPLY_SELECTED_CORRECTIONS",
    })
    assert result.status_code == 409 and result.json()["error"]["code"] == "repair_read_only"
    assert client.post(f"{ROOT}/cases/{case_id}/demo-change", headers=_auth(), json={"action": "refund_one"}).status_code == 409


def test_invalid_input_errors_never_echo_raw_fields(tmp_path):
    client, _ = _client(tmp_path)
    data = demo_snapshot().model_dump(mode="json")
    secret = "untrusted-sensitive-material-never-echo-this"
    data["purchases"][0]["access_present"] = secret
    response = client.post(ROOT + "/cases", headers=_auth(), json={"snapshot": data})
    assert response.status_code == 422
    assert secret not in response.text
    assert client.get(ROOT + "/cases", headers=_auth()).json() == {"cases": []}


def test_missing_case_and_invalid_history_bounds_are_clear(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get(f"{ROOT}/cases/{'f' * 32}", headers=_auth()).json()["error"]["code"] == "repair_case_not_found"
    assert client.get(ROOT + "/cases?limit=1000", headers=_auth()).status_code == 422
    assert client.get(ROOT + "/cases?offset=-1", headers=_auth()).status_code == 422


def test_api_uses_configured_external_managed_storage(tmp_path):
    from dataclasses import replace

    client, _ = _client(tmp_path / "workspace")
    runtime = tmp_path / "external-runtime"
    service = client.app.state.query_service
    service.config = replace(service.config, state_dir=str(runtime))
    assert client.post(ROOT + "/demo", headers=_auth()).status_code == 200
    assert (runtime / "customer-repair" / "customer-repair.db").exists()
    assert not (tmp_path / "workspace" / ".syndra" / "customer-repair").exists()


def test_demo_account_reads_actual_access_before_and_after_correction(tmp_path):
    client, _ = _client(tmp_path)
    case_id = client.post(ROOT + "/demo", headers=_auth()).json()["case"]["summary"]["case_id"]
    route = f"{ROOT}/cases/{case_id}/demo-account/purchase-001"
    assert client.get(route, headers=_auth()).json()["allowed"] is False
    unknown = client.get(f"{ROOT}/cases/{case_id}/demo-account/purchase-091", headers=_auth()).json()
    assert unknown["allowed"] is None
    preview = client.post(f"{ROOT}/cases/{case_id}/preview", headers=_auth()).json()["preview"]
    response = client.post(f"{ROOT}/cases/{case_id}/apply", headers=_auth(), json={
        "preview_id": preview["preview_id"], "preview_fingerprint": preview["fingerprint"], "request_id": uuid.uuid4().hex,
        "purchase_ids": ["purchase-001"], "confirmation": "APPLY_SELECTED_CORRECTIONS",
    })
    assert response.status_code == 200
    assert client.get(route, headers=_auth()).json()["allowed"] is True


def test_import_cannot_claim_a_live_account_read(tmp_path):
    client, _ = _client(tmp_path)
    case_id = client.post(ROOT + "/cases", headers=_auth(), json={"snapshot": demo_snapshot().model_dump(mode="json")}).json()["case"]["summary"]["case_id"]
    assert client.get(f"{ROOT}/cases/{case_id}/demo-account/purchase-001", headers=_auth()).status_code == 409


def test_apply_requires_exact_confirmation_and_never_retries_lost_response(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    case_id = client.post(ROOT + "/demo", headers=_auth()).json()["case"]["summary"]["case_id"]
    preview = client.post(f"{ROOT}/cases/{case_id}/preview", headers=_auth()).json()["preview"]
    body = dict(preview_id=preview["preview_id"], preview_fingerprint=preview["fingerprint"], request_id=uuid.uuid4().hex,
                purchase_ids=["purchase-001"], confirmation="yes")
    assert client.post(f"{ROOT}/cases/{case_id}/apply", headers=_auth(), json=body).status_code == 422
    original = client.app.state.customer_repair.apply

    def lose_response(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("A response was lost after commit.")

    monkeypatch.setattr(client.app.state.customer_repair, "apply", lose_response)
    response = client.post(f"{ROOT}/cases/{case_id}/apply", headers=_auth(), json={**body, "confirmation": "APPLY_SELECTED_CORRECTIONS"})
    assert response.status_code == 500
    assert "after commit" not in response.text
    saved = client.get(f"{ROOT}/cases/{case_id}/batches/{body['request_id']}", headers=_auth())
    assert saved.json()["batch"]["restored_count"] == 1
