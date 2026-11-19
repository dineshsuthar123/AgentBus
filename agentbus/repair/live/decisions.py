from datetime import datetime

from agentbus.repair.live.contracts import Decision, Identity, PurchaseState, RepairPolicy


def decide(state: PurchaseState, policy: RepairPolicy, identity: Identity, at: datetime) -> Decision:
    disposition, reason, uncertainty = "eligible", "Paid purchase is missing its original, currently valid feature access.", None
    age = (at - state.observed_at).total_seconds()
    if state.identity != identity:
        disposition, reason = "conflicted_stale", "Application, tenant, environment or configuration identity changed."
    elif age < -5 or age > policy.maximum_age_seconds:
        disposition, reason = "conflicted_stale", "Authoritative evidence is stale or has an invalid observation time."
    elif not state.mapping_verified or not state.account_id or not state.history_complete or state.access_present is None:
        disposition, reason = "investigation_required", "Verified account mapping, complete history and known access are required."
    elif policy.payment_authority == "application" and (state.stripe_customer or state.stripe_invoice or state.stripe_subscription or state.payment_id.startswith(("pi_", "ch_"))):
        disposition, reason = "investigation_required", "Known external payment evidence cannot be bypassed by choosing application authority. This workflow needs a separately coordinated external-payment contract."
    elif not policy.incident_start <= state.purchased_at < policy.incident_end:
        disposition, reason = "investigation_required", "The purchase is outside the reviewed incident interval."
    elif state.pending_payment_change:
        disposition, reason = "conflicted_stale", "A coordinated payment change is pending."
    elif state.restriction != "none" or state.disputed or state.cancellation == "immediate":
        disposition, reason = "subsequent_history", "A restriction, dispute, immediate cancellation or product change forbids restoration."
    elif 0 < state.refunded_minor < state.amount_minor:
        disposition, reason = "investigation_required", "A partial refund requires a separately supported business policy."
    elif state.refunded_minor or state.payment_state in {"cancelled", "failed"}:
        disposition, reason = "subsequent_history", "Refunded, cancelled or failed payments do not authorize restored access."
    elif state.payment_state != "paid":
        disposition, reason = "investigation_required", "Authorization or pending settlement is not a successful payment."
    elif not state.period_start <= at < state.period_end:
        disposition, reason = "investigation_required", "Historical or future access periods need a supported compensation policy."
    elif state.access_present:
        disposition, reason = "already_correct", "The application already provides the purchased access."
    if disposition in {"investigation_required", "conflicted_stale"}:
        uncertainty = "No write is authorized until fresh, complete evidence resolves this condition."
    return Decision(purchase_id=state.purchase_id, disposition=disposition, reason=reason, state=state, uncertainty=uncertainty)
