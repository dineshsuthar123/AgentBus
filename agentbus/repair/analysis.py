from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from agentbus.repair.models import PurchaseEvidence, RepairCounts, RepairObservation, RepairSnapshot


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def analyze(snapshot: RepairSnapshot) -> tuple[list[RepairObservation], RepairCounts]:
    payments: dict[str, set[str]] = defaultdict(set)
    events: dict[str, set[str]] = defaultdict(set)
    targets: dict[tuple, set[bool | None]] = defaultdict(set)
    for purchase in snapshot.purchases:
        payments[purchase.payment_id].add(purchase.purchase_id)
        for event in purchase.evidence_ids:
            events[event].add(purchase.purchase_id)
        if purchase.account_id is not None:
            targets[target(purchase)].add(purchase.access_present)
    observations = []
    for purchase in snapshot.purchases:
        disposition, reason = classify(snapshot, purchase)
        if disposition != "out_of_scope" and (
            len(payments[purchase.payment_id]) > 1
            or any(len(events[event]) > 1 for event in purchase.evidence_ids)
            or (purchase.account_id is not None and len(targets[target(purchase)]) > 1)
        ):
            disposition, reason = "unresolved", "Payment, event, or access evidence conflicts across records. Resolve the mapping first."
        observations.append(RepairObservation(
            purchase_id=purchase.purchase_id, tenant_id=purchase.tenant_id,
            account_id=purchase.account_id, entitlement=purchase.entitlement,
            disposition=disposition, reason=reason, evidence_ids=purchase.evidence_ids,
            observed_revision=purchase.revision, before_access=purchase.access_present,
            proposed_access=True if disposition == "repairable" else None,
        ))
    totals = {key: sum(item.disposition == key for item in observations) for key in (
        "repairable", "already_correct", "ineligible", "unresolved", "out_of_scope",
    )}
    return observations, RepairCounts(total=len(observations), **totals)


def target(purchase: PurchaseEvidence) -> tuple[str, str | None, str]:
    return purchase.tenant_id, purchase.account_id, purchase.entitlement


def classify(snapshot: RepairSnapshot, purchase: PurchaseEvidence):
    if not snapshot.incident_start <= purchase.paid_at < snapshot.incident_end:
        return "out_of_scope", "Purchase is outside the incident interval (start inclusive, end exclusive)."
    if not purchase.mapping_verified or purchase.account_id is None:
        return "unresolved", "The purchase-to-account mapping is not verified. No correction is proposed."
    if not purchase.history_complete:
        return "unresolved", "Later account or payment history is incomplete. Supply the missing evidence."
    if purchase.payment_state == "unknown" or purchase.access_present is None:
        return "unresolved", "Payment or current access state is unknown. Inspect the application before proceeding."
    if purchase.payment_state != "paid":
        return "ineligible", "Refunded or cancelled under the selected policy. Existing access is left unchanged."
    if purchase.access_present:
        return "already_correct", "Access is already present. No correction is necessary."
    return "repairable", "Verified paid purchase is missing access. Restore this entitlement only."
