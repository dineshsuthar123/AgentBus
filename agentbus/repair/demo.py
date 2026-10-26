from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentbus.repair.analysis import fingerprint, target
from agentbus.repair.models import DemoProof, FixReference, PurchaseEvidence, RepairSnapshot
from agentbus.repair.service import AFTER_CODE, BEFORE_CODE


def demo_snapshot() -> RepairSnapshot:
    start = datetime(2026, 1, 15, 9, tzinfo=timezone.utc)
    purchases = []
    for number in range(1, 101):
        purchases.append(PurchaseEvidence(
            purchase_id=f"purchase-{number:03}", payment_id=f"payment-{number:03}",
            tenant_id="workspace-north" if number % 2 else "workspace-south",
            account_id=None if 91 <= number <= 95 else f"account-{number:03}",
            entitlement="pro-workspace", paid_at=start + timedelta(minutes=number),
            payment_state="refunded" if 81 <= number <= 85 else "cancelled" if 86 <= number <= 90 else "paid",
            access_present=66 <= number <= 80,
            mapping_verified=not 91 <= number <= 95, history_complete=number <= 95,
            evidence_ids=[f"event-{number:03}"], revision=1,
        ))
    return RepairSnapshot(
        title="Paid, but still waiting at the door", incident_start=start,
        incident_end=start + timedelta(hours=2), observed_at=start + timedelta(hours=3),
        coverage_complete=True,
        fix=FixReference(before_revision=fingerprint(BEFORE_CODE), after_revision=fingerprint(AFTER_CODE), review_confirmed=True),
        purchases=purchases,
    )


def accept_broken(connection, case_id, purchase):
    connection.execute("INSERT INTO repair_purchases VALUES (?, ?, ?)", (case_id, purchase.purchase_id, purchase.model_dump_json()))
    if purchase.account_id is not None:
        connection.execute("INSERT OR IGNORE INTO repair_access VALUES (?, ?, ?, ?, 0, 1)", (case_id, *target(purchase)))
    return {"accepted": True}


def accept_fixed(connection, case_id, purchase):
    accept_broken(connection, case_id, purchase)
    if purchase.payment_state == "paid" and purchase.mapping_verified and purchase.history_complete and purchase.account_id:
        connection.execute("UPDATE repair_access SET present = 1, revision = revision + 1 WHERE case_id = ? AND tenant_id = ? AND account_id = ? AND entitlement = ? AND present = 0", (case_id, *target(purchase)))
    return {"accepted": True}


def exercise_fix(connection, case_id, snapshot):
    def missing():
        return sum(
            purchase.payment_state == "paid" and purchase.mapping_verified and purchase.history_complete
            and not connection.execute("SELECT present FROM repair_access WHERE case_id = ? AND tenant_id = ? AND account_id = ? AND entitlement = ?", (case_id, *target(purchase))).fetchone()[0]
            for purchase in snapshot.purchases
        )

    before = missing()
    canary = PurchaseEvidence(purchase_id="new-purchase-before", payment_id="new-payment-before", tenant_id="canary",
                              account_id="new-account-before", entitlement="pro-workspace", paid_at=snapshot.observed_at,
                              payment_state="paid", access_present=False, mapping_verified=True, history_complete=True,
                              evidence_ids=["canary-event-before"], revision=1)
    accept_broken(connection, case_id, canary)
    old_access = connection.execute("SELECT present FROM repair_access WHERE case_id = ? AND account_id = ?", (case_id, canary.account_id)).fetchone()[0]
    updated = canary.model_copy(update={"purchase_id": "new-purchase-after", "payment_id": "new-payment-after", "account_id": "new-account-after", "evidence_ids": ["canary-event-after"]})
    accept_fixed(connection, case_id, updated)
    new_access = connection.execute("SELECT present FROM repair_access WHERE case_id = ? AND account_id = ?", (case_id, updated.account_id)).fetchone()[0]
    if old_access or not new_access:
        raise RuntimeError("The owned demo application did not reproduce the expected fixture behavior.")
    return DemoProof(historical_missing_before_fix=before, historical_missing_after_fix=missing(), before_code=BEFORE_CODE, after_code=AFTER_CODE)
