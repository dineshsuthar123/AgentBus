from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from pydantic import ValidationError

from agentbus.repair.analysis import analyze, fingerprint
from agentbus.repair.demo import demo_snapshot
from agentbus.repair.models import RepairSnapshot
from agentbus.repair.service import RepairError, RepairService, verify_package


def setup_case(tmp_path, **kwargs):
    service = RepairService(tmp_path / "managed", **kwargs)
    case = service.create(demo_snapshot(), demo=True)
    preview = service.preview(case.summary.case_id)
    return service, case, preview


def apply_arguments(preview, **overrides):
    return dict(preview_id=preview.preview_id, preview_fingerprint=preview.fingerprint,
                request_id=uuid.uuid4().hex,
                purchase_ids=[row.purchase_id for row in preview.observations if row.disposition == "repairable"], **overrides)


def test_demo_reproduces_forward_fix_without_correcting_history(tmp_path):
    service, case, preview = setup_case(tmp_path)
    assert case.demo_proof.broken_handler_granted_access is False
    assert case.demo_proof.fixed_handler_granted_access is True
    assert case.demo_proof.historical_missing_before_fix == 65
    assert case.demo_proof.historical_missing_after_fix == 65
    assert preview.counts.model_dump() == dict(total=100, repairable=65, already_correct=15, ineligible=10, unresolved=10, out_of_scope=0)
    assert len(service.get(case.summary.case_id).snapshot.purchases) == 100


def test_manual_corrections_after_preview_are_preserved(tmp_path):
    service, case, preview = setup_case(tmp_path)
    case_id = case.summary.case_id
    assert service.demo_change(case_id, "support_corrects_five") == 5
    assert service.demo_change(case_id, "support_corrects_five") == 0
    batch = service.apply(case_id, **apply_arguments(preview))
    assert (batch.restored_count, batch.skipped_count, batch.unresolved_count) == (60, 5, 0)
    after = service.preview(case_id)
    assert after.counts.repairable == 0
    assert after.counts.already_correct == 80
    assert after.counts.ineligible == 10
    assert after.counts.unresolved == 10
    assert verify_package(service.export(case_id, preview.preview_id))


def test_refund_after_preview_does_not_restore_access(tmp_path):
    service, case, preview = setup_case(tmp_path)
    service.demo_change(case.summary.case_id, "refund_one")
    batch = service.apply(case.summary.case_id, **apply_arguments(preview))
    result = next(row for row in batch.results if row.purchase_id == "purchase-001")
    assert result.outcome == "ineligible" and result.verified_access is False
    assert batch.restored_count == 64


def test_repeated_request_returns_immutable_saved_result_after_restart(tmp_path):
    service, case, preview = setup_case(tmp_path)
    arguments = apply_arguments(preview)
    result = service.apply(case.summary.case_id, **arguments)
    restarted = RepairService(tmp_path / "managed")
    assert restarted.apply(case.summary.case_id, **arguments) == result
    assert restarted.batch(case.summary.case_id, arguments["request_id"]) == result
    assert len(restarted.batches(case.summary.case_id)) == 1
    assert len([event for event in restarted.events(case.summary.case_id) if event.kind == "applied"]) == 1


def test_reused_request_id_cannot_change_its_selection(tmp_path):
    service, case, preview = setup_case(tmp_path)
    arguments = apply_arguments(preview)
    service.apply(case.summary.case_id, **arguments)
    with pytest.raises(RepairError, match="different selection"):
        service.apply(case.summary.case_id, **{**arguments, "purchase_ids": ["purchase-001"]})


@pytest.mark.parametrize("mutation", ["wrong_fingerprint", "unreviewed", "duplicate", "cross_case"])
def test_invalid_selections_never_apply_partial_changes(tmp_path, mutation):
    service, case, preview = setup_case(tmp_path)
    arguments = apply_arguments(preview)
    if mutation == "wrong_fingerprint":
        arguments["preview_fingerprint"] = "a" * 64
    elif mutation == "unreviewed":
        arguments["purchase_ids"] += ["purchase-081"]
    elif mutation == "duplicate":
        arguments["purchase_ids"] += ["purchase-001"]
    else:
        other = service.create(demo_snapshot(), demo=True)
        arguments["preview_id"] = service.preview(other.summary.case_id).preview_id
    with pytest.raises(RepairError):
        service.apply(case.summary.case_id, **arguments)
    assert service.preview(case.summary.case_id).counts.repairable == 65
    assert service.batches(case.summary.case_id) == []


def test_expiry_is_checked_without_real_sleeps_and_saved_results_remain_available(tmp_path):
    from datetime import datetime, timezone

    time = [datetime(2026, 10, 26, tzinfo=timezone.utc)]
    service, case, preview = setup_case(tmp_path, clock=lambda: time[0])
    arguments = apply_arguments(preview)
    time[0] += timedelta(minutes=30)
    with pytest.raises(RepairError) as error:
        service.apply(case.summary.case_id, **arguments)
    assert error.value.code == "repair_preview_expired"
    fresh = service.preview(case.summary.case_id)
    arguments = apply_arguments(fresh)
    saved = service.apply(case.summary.case_id, **arguments)
    time[0] += timedelta(hours=1)
    assert service.apply(case.summary.case_id, **arguments) == saved


def test_failure_between_domain_write_and_receipt_rolls_back_both(tmp_path, monkeypatch):
    service, case, preview = setup_case(tmp_path)

    def fail(*_):
        raise RuntimeError("controlled failure")

    monkeypatch.setattr(service, "_after_grant", fail)
    with pytest.raises(RuntimeError, match="controlled failure"):
        service.apply(case.summary.case_id, **apply_arguments(preview))
    assert service.preview(case.summary.case_id).counts.repairable == 65
    assert service.batches(case.summary.case_id) == []


def test_concurrent_clients_do_not_duplicate_account_corrections(tmp_path):
    service, case, preview = setup_case(tmp_path)
    another = RepairService(tmp_path / "managed")
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(instance.apply, case.summary.case_id, **apply_arguments(preview)) for instance in (service, another)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(result.restored_count for result in results) == 65
    assert sum(result.skipped_count for result in results) == 65
    assert len(service.batches(case.summary.case_id)) == 2


def test_imports_preserve_observation_time_and_never_mutate_the_source(tmp_path):
    service = RepairService(tmp_path)
    original = demo_snapshot()
    case = service.create(original)
    preview = service.preview(case.summary.case_id)
    assert case.summary.mode == "imported_snapshot"
    assert preview.observed_at == original.observed_at
    with pytest.raises(RepairError) as error:
        service.apply(case.summary.case_id, **apply_arguments(preview))
    assert error.value.code == "repair_read_only"
    with pytest.raises(RepairError):
        service.demo_change(case.summary.case_id, "refund_one")
    assert service.get(case.summary.case_id).snapshot == original
    assert verify_package(service.export(case.summary.case_id, preview.preview_id))


def test_deduplicated_events_and_tenant_identity_are_explicit():
    raw = demo_snapshot().model_dump(mode="json")
    raw["purchases"] = raw["purchases"][:2]
    raw["purchases"][0]["evidence_ids"] *= 2
    raw["purchases"][1]["account_id"] = raw["purchases"][0]["account_id"]
    snapshot = RepairSnapshot.model_validate(raw)
    assert len(snapshot.purchases[0].evidence_ids) == 1
    assert analyze(snapshot)[1].repairable == 2
    raw["purchases"][1]["payment_id"] = raw["purchases"][0]["payment_id"]
    assert analyze(RepairSnapshot.model_validate(raw))[1].unresolved == 2


def test_shared_events_and_inconsistent_target_observations_require_investigation():
    raw = demo_snapshot().model_dump(mode="json")
    raw["purchases"] = raw["purchases"][:2]
    raw["purchases"][1]["evidence_ids"] = raw["purchases"][0]["evidence_ids"]
    assert analyze(RepairSnapshot.model_validate(raw))[1].unresolved == 2
    raw["purchases"][1]["evidence_ids"] = ["unique-event"]
    raw["purchases"][1].update(tenant_id=raw["purchases"][0]["tenant_id"], account_id=raw["purchases"][0]["account_id"], access_present=True)
    assert analyze(RepairSnapshot.model_validate(raw))[1].unresolved == 2


def test_multiple_purchases_for_one_entitlement_grant_only_once(tmp_path):
    raw = demo_snapshot().model_dump(mode="json")
    raw["purchases"] = raw["purchases"][:2]
    raw["purchases"][1].update(tenant_id=raw["purchases"][0]["tenant_id"], account_id=raw["purchases"][0]["account_id"])
    service = RepairService(tmp_path)
    case = service.create(RepairSnapshot.model_validate(raw), demo=True)
    preview = service.preview(case.summary.case_id)
    batch = service.apply(case.summary.case_id, **apply_arguments(preview))
    assert batch.restored_count == 1 and batch.skipped_count == 1


def test_changed_evidence_requires_a_fresh_preview(tmp_path):
    service, case, preview = setup_case(tmp_path)
    with service._connection(write=True) as connection:
        row = connection.execute("SELECT payload FROM repair_purchases WHERE case_id = ? AND purchase_id = 'purchase-001'", (case.summary.case_id,)).fetchone()
        from agentbus.repair.models import PurchaseEvidence
        purchase = PurchaseEvidence.model_validate_json(row[0])
        changed = purchase.model_copy(update={"revision": purchase.revision + 1})
        connection.execute("UPDATE repair_purchases SET payload = ? WHERE case_id = ? AND purchase_id = ?", (changed.model_dump_json(), case.summary.case_id, purchase.purchase_id))
    batch = service.apply(case.summary.case_id, **apply_arguments(preview))
    assert batch.status == "needs_attention" and batch.unresolved_count == 1
    assert next(row for row in batch.results if row.purchase_id == "purchase-001").outcome == "conflict"


def test_incident_window_is_half_open_and_partial_coverage_stays_visible():
    snapshot = demo_snapshot()
    raw = snapshot.model_dump(mode="json")
    raw["coverage_complete"] = False
    raw["purchases"] = raw["purchases"][:2]
    raw["purchases"][0]["paid_at"] = snapshot.incident_start.isoformat()
    raw["purchases"][1]["paid_at"] = snapshot.incident_end.isoformat()
    validated = RepairSnapshot.model_validate(raw)
    assert validated.coverage_complete is False
    assert analyze(validated)[1].repairable == 1
    assert analyze(validated)[1].out_of_scope == 1


@pytest.mark.parametrize("change", ["naive", "future", "range", "duplicate", "flag", "policy", "extra", "review", "same_fix"])
def test_invalid_evidence_fails_before_storage(change):
    raw = demo_snapshot().model_dump(mode="json")
    if change == "naive": raw["observed_at"] = "2026-01-15T12:00:00"
    if change == "future": raw["purchases"][0]["paid_at"] = "2030-01-01T12:00:00Z"
    if change == "range": raw["incident_end"] = raw["incident_start"]
    if change == "duplicate": raw["purchases"].append(raw["purchases"][0])
    if change == "flag": raw["purchases"][0]["access_present"] = "false"
    if change == "policy": raw["policy"]["refunded_access"] = "restore"
    if change == "extra": raw["api_key"] = "must-never-be-accepted"
    if change == "review": raw["fix"]["review_confirmed"] = 1
    if change == "same_fix": raw["fix"]["after_revision"] = raw["fix"]["before_revision"]
    with pytest.raises(ValidationError):
        RepairSnapshot.model_validate(raw)


def test_export_detects_changed_facts_even_when_the_outer_checksum_is_replaced(tmp_path):
    service, case, preview = setup_case(tmp_path)
    package = service.export(case.summary.case_id, preview.preview_id)
    altered = package.model_copy(update={"snapshot": package.snapshot.model_copy(update={"coverage_complete": False})})
    altered = altered.model_copy(update={"checksum": fingerprint(altered.model_dump(mode="json", exclude={"checksum"}))})
    assert not verify_package(altered)


def test_export_consistency_rejects_invented_batch_totals(tmp_path):
    service, case, preview = setup_case(tmp_path)
    service.apply(case.summary.case_id, **apply_arguments(preview))
    package = service.export(case.summary.case_id, preview.preview_id)
    invented = package.batches[0].model_copy(update={"restored_count": 100})
    altered = package.model_copy(update={"batches": [invented]})
    altered = altered.model_copy(update={"checksum": fingerprint(altered.model_dump(mode="json", exclude={"checksum"}))})
    assert not verify_package(altered)


def test_preview_fingerprint_validation_rejects_non_ascii_input_safely(tmp_path):
    service, case, preview = setup_case(tmp_path)
    arguments = apply_arguments(preview)
    arguments["preview_fingerprint"] = "\u00e9" * 64
    with pytest.raises(RepairError, match="valid preview"):
        service.apply(case.summary.case_id, **arguments)


def test_managed_storage_does_not_create_repository_artifacts(tmp_path):
    workspace = tmp_path / "repository"
    workspace.mkdir()
    service = RepairService(tmp_path / "external-runtime" / "customer-repair")
    case = service.create(demo_snapshot(), demo=True)
    service.preview(case.summary.case_id)
    assert list(workspace.iterdir()) == []
    assert (tmp_path / "external-runtime" / "customer-repair" / "customer-repair.db").exists()


def test_readers_keep_a_consistent_snapshot_while_another_client_writes(tmp_path):
    service, case, _ = setup_case(tmp_path)
    with service._connection() as reader:
        before = reader.execute("SELECT COUNT(*) FROM repair_access WHERE case_id = ? AND present = 1", (case.summary.case_id,)).fetchone()[0]
        assert service.demo_change(case.summary.case_id, "support_corrects_five") == 5
        after = reader.execute("SELECT COUNT(*) FROM repair_access WHERE case_id = ? AND present = 1", (case.summary.case_id,)).fetchone()[0]
        assert before == after
    assert service.preview(case.summary.case_id).counts.already_correct == 20


def test_noop_demo_changes_do_not_create_unbounded_events(tmp_path):
    service, case, _ = setup_case(tmp_path)
    service.demo_change(case.summary.case_id, "support_corrects_five")
    count = len(service.events(case.summary.case_id))
    for _ in range(5):
        assert service.demo_change(case.summary.case_id, "support_corrects_five") == 0
    assert len(service.events(case.summary.case_id)) == count


def test_credential_shaped_identifiers_are_rejected_before_persistence():
    raw = demo_snapshot().model_dump(mode="json")
    raw["purchases"][0]["payment_id"] = "sk-test-" + "a" * 40
    with pytest.raises(ValidationError, match="credential material"):
        RepairSnapshot.model_validate(raw)
