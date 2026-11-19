from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
import threading
import uuid

import pytest
from pydantic import SecretStr

from agentbus.execution.state_store import StateStore
from agentbus.repair.demo import demo_snapshot
from agentbus.repair.integration.postgres import PostgresApplication
from agentbus.repair.integration.reference import fixture
from agentbus.repair.integration.server import Principal, create_correction_app
from agentbus.repair.live.contracts import ConnectionManifest, Identity, RepairPolicy, now
from agentbus.repair.live.service import ConnectedRepairService
from agentbus.repair.live.transport import ApplicationConnector
from agentbus.repair.service import RepairService


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    pytest.importorskip("psycopg")
    from agentbus.repair.integration.testing import disposable_postgres
    if os.environ.get("SYNDRA_TEST_POSTGRES") != "1":
        pytest.skip("Run with SYNDRA_TEST_POSTGRES=1 to start an owned real PostgreSQL cluster.")
    with disposable_postgres(tmp_path_factory.mktemp("repair-pg")) as (dsn, _directory):
        yield dsn


@pytest.fixture
def connected(pg, tmp_path, monkeypatch):
    import socket
    import uvicorn
    identity = Identity(application="reference", tenant=uuid.uuid4().hex, environment="sandbox", configuration_revision="v1")
    application = PostgresApplication(pg, identity)
    application.migrate()
    application.seed(fixture(identity))
    while application.consume_outbox():
        pass
    token = "disposable-" + uuid.uuid4().hex
    monkeypatch.setenv("SYNDRA_REPAIR_TEST_APPLICATION", token)
    app = create_correction_app(application, [Principal("test-service", SecretStr(token), frozenset({"read", "approve", "repair", "verify", "revoke"}))], process_outbox=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", access_log=False))
    ready = threading.Event()
    original = server.startup
    async def startup(*args, **kwargs):
        await original(*args, **kwargs)
        ready.set()
    server.startup = startup
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    assert ready.wait(10), "Separate HTTP service must start before connector requests."
    base = RepairService(tmp_path / "repair")
    snapshot = demo_snapshot()
    # These are untrusted candidate records, not target authority.
    case = base.create(snapshot)
    service = ConnectedRepairService(base, tmp_path, StateStore(tmp_path / "runtime.db"))
    manifest = ConnectionManifest(connection_id="reference", identity=identity,
        destination=f"http://127.0.0.1:{port}/syndra-correction/v1", approved_addresses=["127.0.0.1"],
        credential_ref="env:SYNDRA_REPAIR_TEST_APPLICATION", revision=1, transport="disposable_loopback",
        permissions=["read", "approve", "repair", "verify", "revoke"])
    service.configure(manifest)
    at = now()
    policy = RepairPolicy(incident_start=at - timedelta(days=2), incident_end=at, maximum_writes=100, maximum_age_seconds=300, requests_per_second=10)
    preview = service.refresh_ids(case.summary.case_id, [f"purchase-{i:05}" for i in range(100)], "reference", policy)
    try:
        yield service, application, case.summary.case_id, preview, manifest
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()


def plan_for(connected, ids):
    service, _application, case_id, preview, _manifest = connected
    value = service.create_plan(case_id, preview["scan_id"], ids, trusted=True)
    service.approve(value["plan"]["plan_id"], value["plan"]["digest"])
    return value["plan"]["plan_id"]


def test_actual_network_conditional_receipts_and_normal_access(connected):
    service, application, _case_id, preview, _manifest = connected
    assert preview["counts"] == {"eligible": 60, "already_correct": 20, "subsequent_history": 10, "investigation_required": 10}
    plan_id = plan_for(connected, ["purchase-00000"])
    result = service.execute(plan_id)
    assert result["counts"]["committed"] == 1
    assert result["counts"]["verified"] == 0
    assert result["counts"]["verification_pending"] == 1
    application.consume_outbox()
    result = service.execute(plan_id)
    assert result["counts"]["verified"] == 1
    assert result["outcomes"][0]["attempts"] == 1
    assert application.access("purchase-00000")["allowed"]


def test_support_after_preview_and_later_refund(connected):
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000", "purchase-00001"])
    application.business_action("purchase-00001", "support_grant")
    result = service.execute(plan_id)
    assert result["counts"]["committed"] == 1
    assert result["counts"]["not_applied"] == 1
    assert {r["receipt"]["reason"] for r in result["outcomes"]} == {"granted", "already_correct"}
    application.business_action("purchase-00000", "refund")
    application.consume_outbox()
    result = service.execute(plan_id)
    assert result["counts"]["superseded"] == 1
    assert not application.access("purchase-00000")["allowed"]


def test_refund_races_aggregate_lock_not_only_entitlement_row(connected):
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    locked, release = threading.Event(), threading.Event()
    def barrier(_operation, _connection):
        locked.set()
        assert release.wait(10)
    application.before_commit = barrier
    with ThreadPoolExecutor(2) as pool:
        repairing = pool.submit(service.execute, plan_id)
        assert locked.wait(10)
        refunding = pool.submit(application.business_action, "purchase-00000", "refund")
        release.set()
        repairing.result(timeout=20)
        refunding.result(timeout=20)
    application.before_commit = None
    application.consume_outbox()
    result = service.execute(plan_id)
    assert result["counts"]["committed"] == 1
    assert result["counts"]["superseded"] == 1
    assert not application.access("purchase-00000")["allowed"]


def test_timeout_after_commit_restarts_and_reconciles_without_second_effect(connected):
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    class LostResponse(ApplicationConnector):
        def apply(self, operation):
            from agentbus.repair.live.transport import ConnectorError
            super().apply(operation)
            raise ConnectorError()
    service.connector_factory = LostResponse
    service.execute(plan_id)
    application.consume_outbox()
    restarted = ConnectedRepairService(service.base, service.workspace, service.store)
    result = restarted.execute(plan_id)
    assert result["counts"]["committed"] == result["counts"]["verified"] == 1
    assert result["outcomes"][0]["attempts"] == 1
    with application.connect() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM syndra_receipt WHERE tenant=%s AND payload->>'outcome'='committed'", (application.identity.tenant,)).fetchone()["n"] == 1


def test_revoke_fences_delayed_original_request(connected):
    service, application, *_ = connected
    from agentbus.repair.live.contracts import ConditionalOperation
    from agentbus.repair.integration.postgres import DomainConflict
    plan_id = plan_for(connected, ["purchase-00000"])
    operation = ConditionalOperation.model_validate(service.plan(plan_id)["plan"]["operations"][0])
    service.stop(plan_id)
    with pytest.raises(DomainConflict):
        application.apply(operation, "test-service")
    assert not application.access("purchase-00000")["allowed"]


def test_overlapping_cases_and_delayed_duplicate_have_one_business_effect(connected):
    service, application, case_id, preview, manifest = connected
    from agentbus.repair.live.contracts import ConditionalOperation
    first = plan_for(connected, ["purchase-00000"])
    second_value = service.create_plan(case_id, preview["scan_id"], ["purchase-00000"], trusted=True)
    second = second_value["plan"]["plan_id"]
    service.approve(second, second_value["plan"]["digest"])
    operation = ConditionalOperation.model_validate(service.plan(first)["plan"]["operations"][0])
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(service.execute, [first, second]))
    assert sum(result["counts"].get("committed", 0) for result in results) == 1
    receipt = ApplicationConnector(manifest).apply(operation)
    assert receipt.operation_id == operation.operation_id
    assert application.purchase("purchase-00000").revision == 2


def test_reference_100_records_60_actual_writes(connected):
    service, application, *_ = connected
    plan_id = plan_for(connected, [f"purchase-{i:05}" for i in range(60)])
    result = service.execute(plan_id)
    assert result["counts"]["committed"] == 60
    while application.consume_outbox():
        pass
    result = service.reconcile(plan_id)
    assert result["counts"]["verified"] == 60
    with application.connect() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM syndra_receipt WHERE tenant=%s AND payload->>'outcome'='committed'", (application.identity.tenant,)).fetchone()["n"] == 60
    assert service.store.get_run(plan_id).status.value == "succeeded"


def test_five_support_fixes_are_not_counted_as_syndra_writes(connected):
    service, application, *_ = connected
    plan_id = plan_for(connected, [f"purchase-{i:05}" for i in range(60)])
    for i in range(5):
        application.business_action(f"purchase-{i:05}", "support_grant")
    result = service.execute(plan_id)
    assert result["counts"]["committed"] == 55
    assert result["counts"]["not_applied"] == 5
    assert sum(row["receipt"]["reason"] == "already_correct" for row in result["outcomes"]) == 5


@pytest.mark.parametrize("action", ["refund", "cancel_immediate", "suspend", "delete", "manual_override", "change_product", "payment_change_pending"])
def test_business_changes_between_approval_and_commit_never_grant(connected, action):
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    application.business_action("purchase-00000", action)
    result = service.execute(plan_id)
    assert result["counts"].get("committed", 0) == 0
    assert result["counts"]["not_applied"] == 1
    assert not application.access("purchase-00000")["allowed"]


def test_request_reuse_changed_payload_and_wrong_principal_rejected(connected):
    from agentbus.repair.integration.postgres import DomainConflict
    from agentbus.repair.live.contracts import ConditionalOperation
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    operation = ConditionalOperation.model_validate(service.plan(plan_id)["plan"]["operations"][0])
    with pytest.raises(DomainConflict):
        application.apply(operation, "different-principal")
    service.execute(plan_id)
    with pytest.raises(DomainConflict):
        application.apply(operation.model_copy(update={"payload_digest": "a" * 64}), "test-service")


def test_connection_rotation_fences_existing_target_approval(connected):
    from agentbus.repair.integration.postgres import DomainConflict
    from agentbus.repair.live.contracts import ConditionalOperation
    service, application, _case_id, _preview, manifest = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    operation = ConditionalOperation.model_validate(service.plan(plan_id)["plan"]["operations"][0])
    service.configure(manifest.model_copy(update={"revision": 2}))
    assert service.plan(plan_id)["stopped"]
    with pytest.raises(DomainConflict):
        application.apply(operation, "test-service")


def test_pre_dispatch_crash_dry_run_reconcile_and_replay_never_write(connected):
    from agentbus.execution.engine import DurableExecutionEngine
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    assert service.reconcile(plan_id)["outcomes"][0]["attempts"] == 0
    assert not application.access("purchase-00000")["allowed"]
    with pytest.raises(ValueError, match="coding executor"):
        DurableExecutionEngine(service.store).resume(plan_id)
    with pytest.raises(ValueError, match="coding task runner"):
        DurableExecutionEngine(service.store).run_until_blocked(plan_id)
    restarted = ConnectedRepairService(service.base, service.workspace, service.store)
    assert restarted.execute(plan_id)["counts"]["committed"] == 1


def test_atomic_mutation_receipt_and_outbox_rollback_on_failure(connected):
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    def fail(_operation, _connection):
        raise RuntimeError("owned deterministic fault; not a database success")
    application.before_commit = fail
    result = service.execute(plan_id)
    assert result["counts"]["outcome_unknown"] == 1
    assert not application.access("purchase-00000")["allowed"]
    with application.connect() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM syndra_receipt WHERE tenant=%s", (application.identity.tenant,)).fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) n FROM syndra_outbox WHERE tenant=%s AND revision>1", (application.identity.tenant,)).fetchone()["n"] == 0


def test_10000_authoritative_candidates_are_paged_and_bounded(connected, tmp_path):
    import itertools
    import json
    import platform
    import time
    import tracemalloc
    service, application, case_id, preview, _manifest = connected
    application.seed(itertools.islice(fixture(application.identity, 10000), 100, None))
    tracemalloc.start()
    started = time.monotonic()
    value = service.refresh_ids(case_id, [f"purchase-{i:05}" for i in range(10000)], "reference", RepairPolicy.model_validate(preview["policy"]))
    elapsed = time.monotonic() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert value["examined"] == 10000
    assert value["counts"]["eligible"] == 6000
    assert len(value["decisions"]) == 50
    assert len(service.preview(case_id, offset=9950)["decisions"]) == 50
    assert peak < 128 * 1024 * 1024
    with application.connect() as connection:
        postgres = connection.execute("SHOW server_version").fetchone()["server_version"]
    report = {"records": 10000, "read_page": 100, "ui_page": 50, "concurrency": 1, "seconds": elapsed, "python_peak_bytes": peak,
              "python": platform.python_version(), "platform": platform.platform(), "postgres": postgres, "writes": 0}
    (tmp_path / "scale.json").write_text(json.dumps(report, indent=2))
    print("BOUNDED_SCALE", json.dumps(report))


@pytest.mark.parametrize("field,value", [
    ("expected_revision", 22), ("payment_id", "different-payment"),
    ("workspace_digest", "b" * 64), ("connection_digest", "b" * 64),
])
def test_target_rejects_resealed_operation_not_in_approved_set(connected, field, value):
    from agentbus.repair.integration.postgres import DomainConflict
    from agentbus.repair.live.contracts import ConditionalOperation, digest
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    original = ConditionalOperation.model_validate(service.plan(plan_id)["plan"]["operations"][0])
    changed = original.model_copy(update={field: value})
    # Model-copy is an application-internal seam; the server still compares the
    # entire operation against the immutable approved set before touching state.
    changed = changed.model_copy(update={"payload_digest": digest(changed, exclude={"payload_digest"})})
    with pytest.raises(DomainConflict):
        application.apply(changed, "test-service")
    assert application.purchase("purchase-00000").revision == 1


def test_workspace_scope_changed_and_untrusted_approval_are_rejected(connected, tmp_path):
    from agentbus.repair.service import RepairError
    service, application, case_id, preview, *_ = connected
    with pytest.raises(RepairError, match="Workspace Trust"):
        service.create_plan(case_id, preview["scan_id"], ["purchase-00000"])
    value = service.create_plan(case_id, preview["scan_id"], ["purchase-00000"], trusted=True)
    other = ConnectedRepairService(service.base, tmp_path / "other", service.store)
    with pytest.raises(RepairError):
        other.approve(value["plan"]["plan_id"], value["plan"]["digest"])
    with pytest.raises(RepairError):
        service.approve(value["plan"]["plan_id"], "b" * 64)
    assert application.purchase("purchase-00000").revision == 1


def test_serialization_retry_restarts_entire_transaction_without_duplicate_effect(connected):
    import psycopg
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    calls = []
    def conflict_once(_operation, connection):
        calls.append(connection.info.backend_pid)
        if len(calls) == 1:
            raise psycopg.errors.SerializationFailure("owned deterministic serialization fault")
    application.before_commit = conflict_once
    result = service.execute(plan_id)
    assert result["counts"]["committed"] == 1 and len(calls) == 2
    assert application.purchase("purchase-00000").revision == 2


def test_read_only_and_identity_mismatch_never_enable_apply(connected, monkeypatch):
    from agentbus.repair.service import RepairError
    service, application, case_id, preview, manifest = connected
    service.configure(manifest.model_copy(update={"revision": 2, "permissions": ["read", "verify"]}))
    assert not service.check("reference").write_ready
    # A new scan remains useful to a read-only principal, but cannot create a write plan.
    refreshed = service.refresh_ids(case_id, ["purchase-00000"], "reference", RepairPolicy.model_validate(preview["policy"]))
    assert refreshed["counts"]["eligible"] == 1
    with pytest.raises(RepairError):
        service.create_plan(case_id, refreshed["scan_id"], ["purchase-00000"], trusted=True)
    monkeypatch.setenv("SYNDRA_REPAIR_TEST_APPLICATION", "revoked-disposable-token-" + uuid.uuid4().hex)
    assert not service.check("reference").write_ready
    assert application.purchase("purchase-00000").revision == 1


def test_unknown_404_never_automatically_resends_and_explicit_retry_keeps_same_id(connected):
    service, application, *_ = connected
    from agentbus.repair.live.transport import ConnectorError
    plan_id = plan_for(connected, ["purchase-00000"])
    sent = []
    class DelayedFirst(ApplicationConnector):
        def apply(self, operation):
            sent.append(operation)
            raise ConnectorError()
    service.connector_factory = DelayedFirst
    service.execute(plan_id)
    service.execute(plan_id)
    assert len(sent) == 1 and service.plan(plan_id)["counts"]["outcome_unknown"] == 1
    # Release the delayed original after controller uncertainty, without a new key.
    application.apply(sent[0], "test-service")
    service.connector_factory = ApplicationConnector
    assert service.reconcile(plan_id)["counts"]["committed"] == 1
    assert service.plan(plan_id)["outcomes"][0]["attempts"] == 1


def test_pause_inspect_continue_reuses_exact_approval(connected):
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    assert service.pause(plan_id)["status"] == "paused"
    assert service.execute(plan_id)["outcomes"][0]["attempts"] == 0
    assert service.reconcile(plan_id)["outcomes"][0]["attempts"] == 0
    assert service.pause(plan_id, resume=True)["counts"]["committed"] == 1


def test_unresolved_purchase_cannot_escape_to_new_plan_key(connected):
    service, _application, case_id, preview, _manifest = connected
    from agentbus.repair.service import RepairError
    from agentbus.repair.live.transport import ConnectorError
    plan_id = plan_for(connected, ["purchase-00000"])
    class Unknown(ApplicationConnector):
        def apply(self, _operation): raise ConnectorError()
    service.connector_factory = Unknown
    service.execute(plan_id)
    with pytest.raises(RepairError, match="previous operation"):
        service.create_plan(case_id, preview["scan_id"], ["purchase-00000"], trusted=True)


def test_refresh_invalidates_and_target_fences_old_approval(connected):
    from agentbus.repair.integration.postgres import DomainConflict
    from agentbus.repair.live.contracts import ConditionalOperation
    service, application, case_id, preview, _manifest = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    operation = ConditionalOperation.model_validate(service.plan(plan_id)["plan"]["operations"][0])
    service.refresh_ids(case_id, ["purchase-00000"], "reference", RepairPolicy.model_validate(preview["policy"]))
    with pytest.raises(DomainConflict):
        application.apply(operation, "test-service")


@pytest.mark.parametrize("phase,exit_code", [("before_execute", 74), ("before_dispatch", 71), ("after_target_commit", 72), ("during_verification", 73)])
def test_actual_controller_process_crashes_reconcile_durable_boundaries(connected, phase, exit_code):
    import subprocess
    import sys
    from pathlib import Path
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    database = Path(service.workspace) / "runtime.db"
    worker = Path(__file__).parent / "fixtures" / "connected_repair_crash.py"
    process = subprocess.run([sys.executable, str(worker.resolve()), service.workspace, str(service.base.root), str(database), plan_id, phase],
                             cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(), capture_output=True, timeout=30, shell=False,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert process.returncode == exit_code, "Crash must occur at the intended owned boundary."
    # Crash recovery cannot steal an unexpired lease. Test the explicit lease-expiry
    # boundary directly rather than waiting on real wall-clock time.
    with service.base._connection(write=True) as connection:
        connection.execute("UPDATE repair_live_plans SET lease_until=? WHERE plan_id=?", ((now() - timedelta(seconds=1)).isoformat(), plan_id))
    if phase in {"before_execute", "before_dispatch"}:
        assert not application.access("purchase-00000")["allowed"]
        result = service.execute(plan_id, retry_unknown=True)
        assert result["counts"]["committed"] == 1
    application.consume_outbox()
    result = service.reconcile(plan_id)
    assert result["counts"]["committed"] == result["counts"]["verified"] == 1
    assert application.purchase("purchase-00000").revision == 2


def test_verification_uses_exact_account_feature_and_domain_entitlement(connected):
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    service.execute(plan_id)
    application.consume_outbox()
    assert service.reconcile(plan_id)["counts"]["verified"] == 1
    with application.connect() as connection:
        connection.execute("UPDATE syndra_entitlement SET account_id=%s WHERE tenant=%s AND purchase_id=%s", ("different-account", application.identity.tenant, "purchase-00000"))
    result = service.reconcile(plan_id)
    assert result["counts"]["verification_failed"] == 1
    assert not application.access("purchase-00000")["allowed"]
    # A later observation must not rewrite the successful immutable runtime attempt.
    assert service.store.list_attempts(plan_id, "customer-repair")[0].status.value == "succeeded"


def test_configuration_fence_blocks_new_plans_during_remote_revocation(connected):
    from agentbus.repair.service import RepairError
    service, application, case_id, preview, manifest = connected
    plan_for(connected, ["purchase-00000"])
    entered, release = threading.Event(), threading.Event()
    class Fencing(ApplicationConnector):
        def revoke(self, plan_id):
            entered.set()
            assert release.wait(10)
            return super().revoke(plan_id)
    service.connector_factory = Fencing
    with ThreadPoolExecutor(1) as pool:
        changing = pool.submit(service.configure, manifest.model_copy(update={"revision": 2}))
        assert entered.wait(10)
        try:
            with pytest.raises(RepairError, match="fencing"):
                service.create_plan(case_id, preview["scan_id"], ["purchase-00001"], trusted=True)
        finally:
            release.set()
        changing.result(timeout=10)
    assert application.purchase("purchase-00001").revision == 1


def test_controller_is_killed_during_real_target_request_then_receipt_is_recovered(connected):
    import subprocess
    import sys
    from pathlib import Path
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    entered, release, committed = threading.Event(), threading.Event(), threading.Event()
    original_apply = application.apply
    def barrier(_operation, _connection):
        entered.set()
        assert release.wait(15)
    def observed_apply(*args):
        result = original_apply(*args)
        committed.set()
        return result
    application.before_commit, application.apply = barrier, observed_apply
    worker = Path(__file__).parent / "fixtures" / "connected_repair_crash.py"
    process = subprocess.Popen([sys.executable, str(worker.resolve()), service.workspace, str(service.base.root),
        str(Path(service.workspace) / "runtime.db"), plan_id, "during_request"], cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        assert entered.wait(10)
        process.terminate()
        process.wait(timeout=10)
        release.set()
        assert committed.wait(10)
        application.consume_outbox()
        result = service.reconcile(plan_id)
        assert result["counts"]["committed"] == result["counts"]["verified"] == 1
        assert result["outcomes"][0]["attempts"] == 1
    finally:
        release.set()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_concurrent_reconciliation_preserves_one_terminal_attempt_and_audit(connected, monkeypatch):
    from concurrent.futures import wait, FIRST_COMPLETED
    from agentbus.execution.models import TaskStatus
    service, application, *_ = connected
    plan_id = plan_for(connected, ["purchase-00000"])
    service.execute(plan_id)
    application.consume_outbox()
    writers = threading.Barrier(4)
    task_committed, release = threading.Event(), threading.Event()
    original = service.store.update_task_status
    def simultaneous_terminal_writes(run_id, task_id, status, **kwargs):
        if status == TaskStatus.SUCCEEDED:
            writers.wait(timeout=10)
            value = original(run_id, task_id, status, **kwargs)
            task_committed.set()
            assert release.wait(10)
            return value
        return original(run_id, task_id, status, **kwargs)
    monkeypatch.setattr(service.store, "update_task_status", simultaneous_terminal_writes)
    with ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(service.reconcile, plan_id) for _ in range(4)]
        try:
            assert task_committed.wait(10)
            completed, _pending = wait(futures, timeout=10, return_when=FIRST_COMPLETED)
            assert completed
            for future in completed:
                future.result()
        finally:
            release.set()
        results = [future.result(timeout=10) for future in futures]
    assert all(result["counts"]["verified"] == 1 for result in results)
    assert len(service.store.list_attempts(plan_id, "customer-repair")) == 1
    assert len(service.store.list_tool_audits(plan_id)) == 1
    assert application.purchase("purchase-00000").revision == 2
