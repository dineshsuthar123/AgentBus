from __future__ import annotations

import random
import time
import uuid
from importlib.resources import files

from datetime import timedelta

from agentbus.repair.live.contracts import (
    AccessVerification, Capabilities, ConditionalOperation, Identity, PurchaseState,
    Receipt, TargetPlan, now,
)
from agentbus.repair.live.decisions import decide
from agentbus.repair.service import RepairError


class DomainConflict(RepairError):
    def __init__(self):
        super().__init__("The exact target operation or approval is no longer valid.", code="target_conflict", status=409)


class PostgresApplication:
    """Reference application domain, not a generic SQL adapter for existing apps.

    Every writer takes the purchase aggregate lock, including absent-entitlement
    creation. Plan locks fence delayed requests against completed revocation.
    """

    def __init__(self, dsn: str, identity: Identity, *, before_commit=None):
        self.dsn, self.identity = dsn, identity
        self.before_commit = before_commit

    def connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row,
                               options="-c statement_timeout=5000 -c lock_timeout=3000")

    def migrate(self):
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(742819035)")
            connection.execute(files(__package__).joinpath("migrations/001_purchase_access.sql").read_text())

    def transaction(self, action):
        import psycopg
        for attempt in range(3):
            try:
                with self.connect() as connection:
                    return action(connection)
            except (psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
                if attempt == 2:
                    raise RepairError("Target contention exceeded its bounded transaction retries.", code="target_busy", status=503) from None
                time.sleep(random.uniform(0.005, 0.02) * 2**attempt)
            except psycopg.Error:
                raise RepairError("The application database could not confirm this request. Look up its receipt.", code="target_storage", status=503) from None

    def capabilities(self, principal, permissions):
        return Capabilities(identity=self.identity, principal=principal, permissions=permissions,
                            aggregate_serialization=True, semantic_receipts=True,
                            normal_access_verification=True, maximum_batch=200)

    def register_plan(self, plan: TargetPlan, principal: str):
        if plan.identity != self.identity or plan.expires_at <= now() or plan.expires_at > now() + timedelta(minutes=10):
            raise DomainConflict()
        # External payment changes cannot be locked by this reference domain.
        if any(op.policy.payment_authority != "application" for op in plan.operations):
            raise DomainConflict()
        def action(connection):
            from psycopg.types.json import Jsonb
            connection.execute("INSERT INTO syndra_plan(plan_id,digest,principal,payload) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                               (plan.plan_id, plan.digest, principal, Jsonb(plan.model_dump(mode="json"))))
            row = connection.execute("SELECT * FROM syndra_plan WHERE plan_id=%s FOR UPDATE", (plan.plan_id,)).fetchone()
            if row["digest"] != plan.digest or row["principal"] != principal or row["revoked"]:
                raise DomainConflict()
            return {"plan_id": plan.plan_id, "digest": plan.digest, "status": "active"}
        return self.transaction(action)

    def revoke(self, plan_id, principal):
        def action(connection):
            row = connection.execute("SELECT principal FROM syndra_plan WHERE plan_id=%s FOR UPDATE", (plan_id,)).fetchone()
            if row and row["principal"] != principal:
                raise DomainConflict()
            if row:
                connection.execute("UPDATE syndra_plan SET revoked=true WHERE plan_id=%s", (plan_id,))
            # A missing plan is tombstoned so delayed registration cannot revive it.
            else:
                from psycopg.types.json import Jsonb
                connection.execute("INSERT INTO syndra_plan VALUES (%s,%s,%s,%s,true)", (plan_id, "revoked", principal, Jsonb({})))
            return {"plan_id": plan_id, "status": "revoked"}
        return self.transaction(action)

    def _purchase(self, connection, purchase_id, *, lock=False):
        row = connection.execute("SELECT state,revision FROM syndra_purchase WHERE tenant=%s AND purchase_id=%s" + (" FOR UPDATE" if lock else ""),
                                 (self.identity.tenant, purchase_id)).fetchone()
        if row is None:
            raise RepairError("Purchase is not present in this authorized tenant.", code="target_missing", status=404)
        return PurchaseState.model_validate({**row["state"], "revision": row["revision"], "observed_at": now()})

    def purchase(self, purchase_id):
        return self.transaction(lambda connection: self._purchase(connection, purchase_id))

    def read_page(self, ids, cursor=None):
        from agentbus.repair.live.contracts import PurchasePage
        if len(ids) > 100 or len(ids) != len(set(ids)):
            raise DomainConflict()
        def action(connection):
            # A single statement gives the page one consistent observation snapshot.
            rows = connection.execute("SELECT state,revision FROM syndra_purchase WHERE tenant=%s AND purchase_id=ANY(%s) AND purchase_id>%s ORDER BY purchase_id LIMIT 101",
                                      (self.identity.tenant, ids, cursor or "")).fetchall()
            return PurchasePage(identity=self.identity, records=[PurchaseState.model_validate({**r["state"], "revision": r["revision"], "observed_at": now()}) for r in rows[:100]],
                                next_cursor=rows[99]["state"]["purchase_id"] if len(rows) > 100 else None, complete=len(rows) <= 100)
        return self.transaction(action)

    def lookup(self, operation_id):
        def action(connection):
            row = connection.execute("SELECT payload FROM syndra_receipt WHERE operation_id=%s AND tenant=%s", (operation_id, self.identity.tenant)).fetchone()
            if row is None:
                raise RepairError("No durable operation receipt is visible yet; an in-flight request may still commit.", code="target_receipt_pending", status=404)
            return Receipt.model_validate(row["payload"])
        return self.transaction(action)

    def apply(self, operation: ConditionalOperation, principal: str):
        if operation.identity != self.identity:
            raise DomainConflict()
        def action(connection):
            from psycopg.types.json import Jsonb
            plan_row = connection.execute("SELECT * FROM syndra_plan WHERE plan_id=%s FOR UPDATE", (operation.plan_id,)).fetchone()
            saved = connection.execute("SELECT payload_digest,payload FROM syndra_receipt WHERE operation_id=%s", (operation.operation_id,)).fetchone()
            if saved:
                if saved["payload_digest"] != operation.payload_digest or not plan_row or plan_row["principal"] != principal:
                    raise DomainConflict()
                return Receipt.model_validate(saved["payload"])
            if not plan_row or plan_row["revoked"] or plan_row["principal"] != principal:
                raise DomainConflict()
            plan = TargetPlan.model_validate(plan_row["payload"])
            if (plan.identity != self.identity or now() >= plan.expires_at or
                not any(item == operation for item in plan.operations)):
                raise DomainConflict()
            state = self._purchase(connection, operation.purchase_id, lock=True)
            decision = decide(state, operation.policy, self.identity, now())
            reason, outcome, event_id = "conflict", "not_applied", None
            semantic = connection.execute("SELECT operation_id FROM syndra_receipt WHERE logical_effect=%s AND payload->>'outcome'='committed'", (operation.logical_effect,)).fetchone()
            if semantic:
                reason = "logical_effect_exists"
            elif decision.disposition == "already_correct":
                reason = "already_correct"
            elif decision.disposition == "subsequent_history":
                reason = "subsequent_history"
            elif decision.disposition == "eligible" and state.revision == operation.expected_revision:
                expected_effect = (state.account_id, state.feature, state.period_start, state.period_end)
                actual_effect = (operation.effect.account_id, operation.effect.feature, operation.effect.period_start, operation.effect.period_end)
                if state.payment_id != operation.payment_id or expected_effect != actual_effect:
                    raise DomainConflict()
                state = state.model_copy(update={"access_present": True, "revision": state.revision + 1})
                event_id = uuid.uuid4().hex
                self._save_state(connection, state)
                self._domain_entitlement(connection, state)
                self._outbox(connection, state, event_id)
                connection.execute("INSERT INTO syndra_business_action VALUES (%s,%s,%s,%s,%s,%s)",
                                   (operation.operation_id, self.identity.tenant, state.purchase_id, "syndra_restore", state.revision, now()))
                outcome, reason = "committed", "granted"
            receipt = Receipt(operation_id=operation.operation_id, payload_digest=operation.payload_digest,
                              logical_effect=operation.logical_effect, identity=self.identity, outcome=outcome, reason=reason,
                              purchase_id=operation.purchase_id, payment_id=operation.payment_id, effect=operation.effect,
                              revision=state.revision, committed_at=now(), outbox_id=event_id)
            connection.execute("INSERT INTO syndra_receipt VALUES (%s,%s,%s,%s,%s,%s)",
                               (receipt.operation_id, receipt.payload_digest, receipt.logical_effect, self.identity.tenant,
                                operation.purchase_id, Jsonb(receipt.model_dump(mode="json"))))
            if self.before_commit:
                self.before_commit(operation, connection)
            return receipt
        return self.transaction(action)

    def _save_state(self, connection, state):
        from psycopg.types.json import Jsonb
        connection.execute("UPDATE syndra_purchase SET state=%s,revision=%s WHERE tenant=%s AND purchase_id=%s",
                           (Jsonb(state.model_dump(mode="json")), state.revision, self.identity.tenant, state.purchase_id))

    def _domain_entitlement(self, connection, state):
        if not state.account_id:
            return
        connection.execute("""INSERT INTO syndra_entitlement VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(tenant,purchase_id) DO UPDATE SET present=excluded.present,revision=excluded.revision""",
                           (self.identity.tenant, state.account_id, state.feature, state.purchase_id, state.period_start,
                            state.period_end, bool(state.access_present), state.revision))

    def _outbox(self, connection, state, event_id):
        connection.execute("INSERT INTO syndra_outbox(event_id,tenant,purchase_id,revision) VALUES (%s,%s,%s,%s)",
                           (event_id, self.identity.tenant, state.purchase_id, state.revision))

    def business_action(self, purchase_id, kind, *, action_id=None):
        """Normal application writers, including payment coordination and support.

        Real provider refunds must first enter pending_payment_change here, before
        an external request; uncertain provider outcomes leave the fence in place.
        """
        actions = {"support_grant", "refund", "cancel_immediate", "cancel_period_end", "suspend", "delete", "manual_override", "change_product", "payment_change_pending"}
        if kind not in actions:
            raise DomainConflict()
        action_id = action_id or uuid.uuid4().hex
        def action(connection):
            state = self._purchase(connection, purchase_id, lock=True)
            old = connection.execute("SELECT kind,purchase_id,tenant FROM syndra_business_action WHERE action_id=%s", (action_id,)).fetchone()
            if old:
                if old["kind"] != kind or old["purchase_id"] != purchase_id or old["tenant"] != self.identity.tenant:
                    raise DomainConflict()
                return state
            updates = {"revision": state.revision + 1}
            if kind == "support_grant":
                if state.payment_state != "paid" or state.refunded_minor or state.disputed or state.restriction != "none":
                    raise DomainConflict()
                updates["access_present"] = True
            elif kind == "refund":
                updates.update(refunded_minor=state.amount_minor, access_present=False, pending_payment_change=False)
            elif kind.startswith("cancel_"):
                updates["cancellation"] = "immediate" if kind == "cancel_immediate" else "period_end"
                if kind == "cancel_immediate":
                    updates["access_present"] = False
            elif kind == "payment_change_pending":
                updates["pending_payment_change"] = True
            else:
                updates.update(restriction={"suspend": "suspended", "delete": "deleted", "change_product": "changed_product"}.get(kind, kind), access_present=False)
            state = PurchaseState.model_validate(state.model_copy(update=updates).model_dump())
            self._save_state(connection, state)
            self._domain_entitlement(connection, state)
            self._outbox(connection, state, uuid.uuid4().hex)
            connection.execute("INSERT INTO syndra_business_action VALUES (%s,%s,%s,%s,%s,%s)",
                               (action_id, self.identity.tenant, purchase_id, kind, state.revision, now()))
            return state
        return self.transaction(action)

    def consume_outbox(self, limit=100):
        def action(connection):
            rows = connection.execute("SELECT * FROM syndra_outbox WHERE tenant=%s AND NOT delivered ORDER BY revision,event_id LIMIT %s FOR UPDATE SKIP LOCKED", (self.identity.tenant, min(100, limit))).fetchall()
            for row in rows:
                state = self._purchase(connection, row["purchase_id"], lock=True)
                # Projection writes use current aggregate state, never a stale event's grant.
                connection.execute("""INSERT INTO syndra_access_projection VALUES (%s,%s,%s,%s)
                    ON CONFLICT(tenant,purchase_id) DO UPDATE SET revision=excluded.revision,present=excluded.present
                    WHERE syndra_access_projection.revision<=excluded.revision""",
                                   (self.identity.tenant, state.purchase_id, state.revision, bool(state.access_present)))
                connection.execute("UPDATE syndra_outbox SET delivered=true WHERE event_id=%s", (row["event_id"],))
            return len(rows)
        return self.transaction(action)

    def authorize(self, connection, state):
        row = connection.execute("SELECT * FROM syndra_access_projection WHERE tenant=%s AND purchase_id=%s", (self.identity.tenant, state.purchase_id)).fetchone()
        entitlement = connection.execute("SELECT * FROM syndra_entitlement WHERE tenant=%s AND purchase_id=%s", (self.identity.tenant, state.purchase_id)).fetchone()
        at = now()
        domain_matches = bool(entitlement and entitlement["account_id"] == state.account_id and entitlement["feature"] == state.feature
                              and entitlement["period_start"] == state.period_start and entitlement["period_end"] == state.period_end)
        allowed = bool(domain_matches and entitlement["present"] and row and row["present"] and state.access_present and state.period_start <= at < state.period_end
                       and state.restriction == "none" and state.cancellation != "immediate" and not state.refunded_minor
                       and not state.disputed and not state.pending_payment_change and state.payment_state == "paid")
        return allowed, bool(row and row["revision"] >= state.revision)

    def access(self, purchase_id):
        def action(connection):
            state = self._purchase(connection, purchase_id, lock=True)
            allowed, propagated = self.authorize(connection, state)
            return {"allowed": allowed, "propagated": propagated, "revision": state.revision}
        return self.transaction(action)

    def verify(self, operation_id):
        def action(connection):
            row = connection.execute("SELECT purchase_id,payload FROM syndra_receipt WHERE operation_id=%s AND tenant=%s", (operation_id, self.identity.tenant)).fetchone()
            if row is None:
                raise RepairError("No durable receipt is visible yet.", status=404)
            receipt = Receipt.model_validate(row["payload"])
            state = self._purchase(connection, row["purchase_id"], lock=True)
            allowed, propagated = self.authorize(connection, state)
            legitimate_later = state.revision > receipt.revision and (state.refunded_minor > 0 or state.disputed or state.restriction != "none" or state.cancellation == "immediate")
            scope_matches = (state.account_id, state.feature, state.period_start, state.period_end, state.payment_id) == (
                receipt.effect.account_id, receipt.effect.feature, receipt.effect.period_start, receipt.effect.period_end, receipt.payment_id)
            status = "superseded" if legitimate_later else "failed" if not scope_matches else "passed" if allowed and propagated else "pending" if not propagated else "failed"
            return AccessVerification(operation_id=operation_id, status=status, observed_at=now(), revision=state.revision,
                                      allowed=allowed, reason={"superseded": "A later legitimate business action supersedes the correction.", "passed": "The normal application authorization path permits the original purchased feature.", "pending": "Required access projection is not current yet.", "failed": "The normal authorization path does not provide the purchased access."}[status])
        return self.transaction(action)

    def seed(self, records):
        """Disposable/reference bootstrap only; never exposed on the repair API."""
        def action(connection):
            from psycopg.types.json import Jsonb
            for state in records:
                if state.identity != self.identity:
                    raise DomainConflict()
                connection.execute("INSERT INTO syndra_purchase VALUES (%s,%s,%s,%s)",
                                   (self.identity.tenant, state.purchase_id, state.revision, Jsonb(state.model_dump(mode="json"))))
                self._domain_entitlement(connection, state)
                self._outbox(connection, state, uuid.uuid4().hex)
        self.transaction(action)
