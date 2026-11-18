from __future__ import annotations

from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import threading
import time
import uuid
import random

from pydantic import ValidationError

from agentbus.execution.models import AttemptStatus, RunRecord, RunStatus, TaskSpec, TaskStatus
from agentbus.execution.transitions import InvalidStateTransition
from agentbus.repair.live import approval
from agentbus.repair.live.contracts import (
    ConditionalOperation, ConnectionManifest, ConnectionStatus, Decision, Effect,
    PurchasePage, RepairPolicy, TargetPlan, digest, now,
)
from agentbus.repair.live.decisions import decide
from agentbus.repair.live.transport import ApplicationConnector, ConnectorError, credential_fingerprint
from agentbus.repair.models import Identifier
from agentbus.repair.service import RepairError, RepairService


class ConnectedRepairService:
    """A journal for remote effects using the existing runtime's exact approval.

    Intent commits locally before dispatch. Target receipts are independent. Unknown
    writes are never retried automatically; explicit retry reuses the same payload.
    """

    def __init__(self, base: RepairService, workspace: Path, store, *, connector_factory=ApplicationConnector):
        self.base, self.workspace, self.store = base, str(Path(workspace).resolve()), store
        self.connector_factory = connector_factory
        self.workspace_digest = digest(self.workspace)
        self._init_lock, self._initialized = threading.Lock(), False

    def _ensure(self):
        with self._init_lock:
            if not self._initialized:
                with self.base._connection(write=True) as connection:
                    for statement in _SCHEMA.split(";"):
                        if statement.strip():
                            connection.execute(statement)
                    connection.execute("CREATE TRIGGER IF NOT EXISTS repair_remote_intent_immutable BEFORE UPDATE OF payload,plan_id ON repair_remote_operations WHEN NEW.payload != OLD.payload OR NEW.plan_id != OLD.plan_id BEGIN SELECT RAISE(ABORT,'Remote operation intent is immutable'); END")
                    connection.execute("CREATE TRIGGER IF NOT EXISTS repair_plan_scope_immutable BEFORE UPDATE OF payload,manifest,credential_fingerprint ON repair_live_plans WHEN NEW.payload != OLD.payload OR NEW.manifest != OLD.manifest OR NEW.credential_fingerprint != OLD.credential_fingerprint BEGIN SELECT RAISE(ABORT,'Remote plan scope is immutable'); END")
                    connection.execute("CREATE TRIGGER IF NOT EXISTS repair_receipt_immutable BEFORE UPDATE OF receipt ON repair_remote_operations WHEN OLD.receipt IS NOT NULL AND NEW.receipt IS NOT OLD.receipt BEGIN SELECT RAISE(ABORT,'Remote receipt is immutable'); END")
                self._initialized = True

    def configure(self, manifest: ConnectionManifest):
        """Local CLI only: Studio cannot register a destination or credential."""
        from agentbus.repair.live.transport import validate_destination
        validate_destination(manifest)
        if manifest.stripe and ((manifest.identity.environment == "production") != manifest.stripe.livemode):
            raise RepairError("Stripe test/live mode does not match the application's environment.")
        self._ensure()
        with self.base._connection(write=True) as connection:
            previous = connection.execute("SELECT manifest FROM repair_connections WHERE connection_id=?", (manifest.connection_id,)).fetchone()
            existing_fence = connection.execute("SELECT fenced FROM repair_connection_fences WHERE connection_id=?", (manifest.connection_id,)).fetchone()
            different = bool(previous and ConnectionManifest.model_validate_json(previous[0]) != manifest)
            changing = bool(previous and (different or (existing_fence and existing_fence[0])))
            if different and manifest.revision <= ConnectionManifest.model_validate_json(previous[0]).revision:
                raise RepairError("Changing a connection requires a higher configuration revision.")
            active_plans = []
            if changing:
                connection.execute("INSERT INTO repair_connection_fences VALUES (?,1) ON CONFLICT(connection_id) DO UPDATE SET fenced=1", (manifest.connection_id,))
                active_plans = [row[0] for row in connection.execute("SELECT plan_id FROM repair_live_plans WHERE json_extract(manifest,'$.connection_id')=? AND status NOT IN ('completed','stopped')", (manifest.connection_id,))]
                for plan_id in active_plans:
                    connection.execute("UPDATE repair_live_plans SET stopped=1,status='stopping' WHERE plan_id=?", (plan_id,))
        if changing:
            old_manifest = ConnectionManifest.model_validate_json(previous[0])
            if different and manifest.revision <= old_manifest.revision:
                raise RepairError("Changing a connection requires a higher configuration revision.")
            for plan_id in active_plans:
                # Revoke on the original approved destination. Rotation may use the
                # new scoped credential, but cannot redirect revocation elsewhere.
                fence_manifest = old_manifest.model_copy(update={"credential_ref": manifest.credential_ref})
                self.connector_factory(fence_manifest).revoke(plan_id)
                with self.base._connection(write=True) as connection:
                    connection.execute("UPDATE repair_live_plans SET status='stopped' WHERE plan_id=?", (plan_id,))
                self._close_stopped_runtime(plan_id)
        with self.base._connection(write=True) as connection:
            old = connection.execute("SELECT manifest FROM repair_connections WHERE connection_id=?", (manifest.connection_id,)).fetchone()
            if (old[0] if old else None) != (previous[0] if previous else None):
                raise RepairError("Another owner changed the connection during configuration. Inspect its current revision before retrying.")
            if old and ConnectionManifest.model_validate_json(old[0]) != manifest:
                if manifest.revision <= ConnectionManifest.model_validate_json(old[0]).revision:
                    raise RepairError("Changing a connection requires a higher configuration revision.")
            connection.execute("INSERT INTO repair_connections VALUES (?,?,NULL,NULL) ON CONFLICT(connection_id) DO UPDATE SET manifest=excluded.manifest,status=NULL,credential_fingerprint=NULL",
                               (manifest.connection_id, manifest.model_dump_json()))
            connection.execute("INSERT INTO repair_connection_fences VALUES (?,0) ON CONFLICT(connection_id) DO UPDATE SET fenced=0", (manifest.connection_id,))
        return manifest

    def manifest(self, connection_id):
        self._ensure()
        with self.base._connection() as connection:
            row = connection.execute("SELECT manifest FROM repair_connections WHERE connection_id=?", (connection_id,)).fetchone()
            fence = connection.execute("SELECT fenced FROM repair_connection_fences WHERE connection_id=?", (connection_id,)).fetchone()
            if fence and fence[0]:
                raise RepairError("Connection reconfiguration is fencing outstanding target approvals. Confirm that stop before creating new work.", code="repair_connection_fenced")
        if row is None:
            raise RepairError("Connection is not locally configured. Use syndra repair connection-add with the reviewed manifest.", status=404)
        return ConnectionManifest.model_validate_json(row[0])

    def connections(self):
        self._ensure()
        with self.base._connection() as connection:
            rows = connection.execute("SELECT * FROM repair_connections ORDER BY connection_id LIMIT 100").fetchall()
            fenced = {row[0] for row in connection.execute("SELECT connection_id FROM repair_connection_fences WHERE fenced=1")}
        statuses = []
        for row in rows:
            manifest = ConnectionManifest.model_validate_json(row["manifest"])
            if manifest.connection_id in fenced:
                statuses.append(ConnectionStatus(manifest=manifest, configured=False, reason="Reconfiguration is fencing outstanding approvals; apply is unavailable."))
                continue
            try:
                fingerprint = credential_fingerprint(manifest)
                configured = True
            except ConnectorError:
                fingerprint, configured = None, False
            if row["status"] and fingerprint == row["credential_fingerprint"]:
                statuses.append(ConnectionStatus.model_validate_json(row["status"]))
            else:
                statuses.append(ConnectionStatus(manifest=manifest, configured=configured,
                                                 reason="Check this connection. Configuration or credentials have not been validated."))
        return statuses

    def check(self, connection_id):
        manifest = self.manifest(connection_id)
        fingerprint = None
        try:
            fingerprint = credential_fingerprint(manifest)
            capabilities = self.connector_factory(manifest).capabilities()
            needed = {"read", "approve", "repair", "verify", "revoke"}
            writable = (needed.issubset(set(capabilities.permissions) & set(manifest.permissions)) and
                        capabilities.aggregate_serialization and capabilities.semantic_receipts and capabilities.normal_access_verification)
            reason = "Validated scoped conditional writes and normal access checks." if writable else "Analysis only: atomic aggregate writers, durable semantic receipts, verification or write permissions are missing."
            if manifest.transport == "disposable_loopback":
                reason += " Disposable sandbox HTTP is not production readiness evidence."
            status = ConnectionStatus(manifest=manifest, configured=True, checked_at=now(), capabilities=capabilities,
                                      write_ready=writable, reason=reason)
        except ConnectorError as error:
            status = ConnectionStatus(manifest=manifest, configured=fingerprint is not None, checked_at=now(), reason=str(error))
        with self.base._connection(write=True) as connection:
            connection.execute("UPDATE repair_connections SET status=?,credential_fingerprint=? WHERE connection_id=? AND manifest=?",
                               (status.model_dump_json(), fingerprint, connection_id, manifest.model_dump_json()))
        return status

    def _ready(self, connection_id, *, writing=False, policy=None):
        status = self.check(connection_id)
        if not status.capabilities or not status.configured:
            raise RepairError(status.reason, code="repair_connection_unavailable", status=409)
        if writing and not status.write_ready:
            raise RepairError(status.reason, code="repair_connection_read_only", status=403)
        if policy and policy.payment_authority == "stripe":
            if not status.manifest.stripe:
                raise RepairError("Stripe authority requires a separately configured read-only Stripe connection.")
            if writing and not status.capabilities.coordinated_external_payments:
                raise RepairError("Analysis only: external Stripe refunds can race the application transaction. Required payment coordination is not available.", code="repair_external_race", status=403)
        return status.manifest

    def refresh(self, case_id, connection_id, policy: RepairPolicy):
        case = self.base.get(case_id)
        if case.summary.mode == "local_demo":
            raise RepairError("Keep the guided demo on its demo target. Import candidate IDs for a connected application.")
        return self.refresh_ids(case_id, [p.purchase_id for p in case.snapshot.purchases], connection_id, policy)

    def refresh_ids(self, case_id, purchase_ids, connection_id, policy: RepairPolicy):
        from pydantic import TypeAdapter
        case = self.base.get(case_id)
        if case.summary.mode != "imported_snapshot":
            raise RepairError("Only an imported candidate case may attach to an application connection.")
        if not 1 <= len(purchase_ids) <= 10000 or len(purchase_ids) != len(set(purchase_ids)):
            raise RepairError("Use 1-10000 distinct candidate purchase identifiers.", status=400)
        try:
            TypeAdapter(list[Identifier]).validate_python(purchase_ids)
        except ValidationError:
            raise RepairError("Use pseudonymous purchase identifiers, not record bodies or credentials.", status=400) from None
        manifest = self._ready(connection_id, policy=policy)
        connector, scan_id = self.connector_factory(manifest), uuid.uuid4().hex
        with self.base._connection() as connection:
            old_plans = [row[0] for row in connection.execute("SELECT plan_id FROM repair_live_plans WHERE case_id=? AND status NOT IN ('completed','stopped')", (case_id,))]
        for plan_id in old_plans:
            self.stop(plan_id)
        with self.base._connection(write=True) as connection:
            if connection.execute("SELECT COUNT(*) FROM repair_scans WHERE case_id=?", (case_id,)).fetchone()[0] >= 50:
                raise RepairError("This case reached 50 authoritative scans. Export evidence or purge resolved scan data before continuing.")
            connection.execute("INSERT INTO repair_scans VALUES (?,?,?,?,?,?,?)", (scan_id, case_id, connection_id, digest(manifest), policy.model_dump_json(), now().isoformat(), "scanning"))
        try:
            for offset in range(0, len(purchase_ids), 100):
                requested = purchase_ids[offset:offset + 100]
                try:
                    page = PurchasePage.model_validate(connector.request("POST", "/purchases", {"purchase_ids": requested}))
                    if page.identity != manifest.identity or not page.complete or page.next_cursor is not None:
                        raise ConnectorError("connector_conflict")
                    records = {state.purchase_id: state for state in page.records}
                    if len(records) != len(page.records) or not set(records).issubset(requested):
                        raise ConnectorError("connector_invalid")
                    decisions = []
                    for identifier in requested:
                        state = records.get(identifier)
                        decision = (decide(state, policy, manifest.identity, now()) if state else
                                    Decision(purchase_id=identifier, disposition="investigation_required", state=None,
                                             reason="The authoritative application did not resolve this candidate.", uncertainty="No verified purchase mapping is available."))
                        if state and policy.payment_authority == "stripe":
                            from agentbus.repair.live.stripe_evidence import StripeEvidence
                            try:
                                StripeEvidence(manifest.stripe).validate_purchase(state)
                            except ConnectorError:
                                decision = Decision(purchase_id=identifier, disposition="conflicted_stale", state=state,
                                                    reason="Stripe evidence is unavailable, contradictory or incomplete.", uncertainty="A CSV and a recent application read cannot replace the configured payment authority.")
                        decisions.append(decision)
                except (ConnectorError, ValidationError):
                    decisions = [Decision(purchase_id=identifier, disposition="conflicted_stale", state=None,
                                          reason="The authoritative page is incomplete, unavailable or invalid.", uncertainty="Refresh complete evidence before creating a plan.") for identifier in requested]
                with self.base._connection(write=True) as connection:
                    connection.executemany("INSERT INTO repair_decisions VALUES (?,?,?)", [(scan_id, item.purchase_id, item.model_dump_json()) for item in decisions])
            with self.base._connection(write=True) as connection:
                connection.execute("UPDATE repair_scans SET status='complete' WHERE scan_id=?", (scan_id,))
                connection.execute("INSERT INTO repair_attached VALUES (?,?) ON CONFLICT(case_id) DO UPDATE SET scan_id=excluded.scan_id", (case_id, scan_id))
        except BaseException:
            with self.base._connection(write=True) as connection:
                connection.execute("UPDATE repair_scans SET status='interrupted' WHERE scan_id=?", (scan_id,))
            raise
        return self.preview(case_id)

    def preview(self, case_id, *, offset=0, limit=50):
        self._ensure()
        if not 0 <= offset <= 10000 or not 1 <= limit <= 100:
            raise RepairError("Use bounded decision pagination.", status=400)
        with self.base._connection() as connection:
            scan = connection.execute("SELECT s.* FROM repair_scans s JOIN repair_attached a ON a.scan_id=s.scan_id WHERE a.case_id=?", (case_id,)).fetchone()
            if scan is None:
                return None
            rows = connection.execute("SELECT payload FROM repair_decisions WHERE scan_id=? ORDER BY purchase_id LIMIT ? OFFSET ?", (scan["scan_id"], limit, offset)).fetchall()
            counts = dict(connection.execute("SELECT json_extract(payload,'$.disposition'),COUNT(*) FROM repair_decisions WHERE scan_id=? GROUP BY 1", (scan["scan_id"],)).fetchall())
        return {"scan_id": scan["scan_id"], "case_id": case_id, "connection_id": scan["connection_id"], "created_at": scan["created_at"],
                "policy": json.loads(scan["policy"]), "counts": counts, "examined": sum(counts.values()), "offset": offset,
                "decisions": [json.loads(row[0]) for row in rows]}

    def create_plan(self, case_id, scan_id, purchase_ids, *, trusted=False):
        if trusted is not True:
            raise RepairError("Explicit Workspace Trust is required for production correction plans.", status=403)
        if not purchase_ids or len(purchase_ids) > 200 or len(set(purchase_ids)) != len(purchase_ids):
            raise RepairError("Select 1-200 distinct authoritative proposals.", status=400)
        current = self.preview(case_id)
        if not current or current["scan_id"] != scan_id:
            raise RepairError("Eligibility changed. Inspect the current scan before creating a plan.")
        policy = RepairPolicy.model_validate(current["policy"])
        if len(purchase_ids) > policy.maximum_writes:
            raise RepairError("The reviewed write cap is smaller than the selected set. Default canary is one record.")
        manifest = self._ready(current["connection_id"], writing=True, policy=policy)
        with self.base._connection() as connection:
            if connection.execute("SELECT COUNT(*) FROM repair_live_plans WHERE case_id=?", (case_id,)).fetchone()[0] >= 50:
                raise RepairError("This case reached 50 exact plans. Preserve its receipts; use a new candidate case for new work.")
            scan = connection.execute("SELECT connection_digest FROM repair_scans WHERE scan_id=?", (scan_id,)).fetchone()
            placeholders = ",".join("?" for _ in purchase_ids)
            unresolved = connection.execute(f"""SELECT o.operation_id FROM repair_remote_operations o
                JOIN repair_live_plans p ON p.plan_id=o.plan_id WHERE o.execution IN ('dispatched','outcome_unknown')
                AND json_extract(o.payload,'$.purchase_id') IN ({placeholders})
                AND json_extract(o.payload,'$.identity.application')=? AND json_extract(o.payload,'$.identity.tenant')=?
                AND json_extract(o.payload,'$.identity.environment')=? LIMIT 1""",
                (*purchase_ids, manifest.identity.application, manifest.identity.tenant, manifest.identity.environment)).fetchone()
            if unresolved:
                raise RepairError("A previous operation for this purchase is unresolved. Reconcile its original receipt; do not create another key.", code="repair_prior_unknown")
            rows = connection.execute(f"SELECT purchase_id,payload FROM repair_decisions WHERE scan_id=? AND purchase_id IN ({placeholders})", (scan_id, *purchase_ids))
            selected = {row["purchase_id"]: Decision.model_validate_json(row["payload"]) for row in rows}
        if scan[0] != digest(manifest) or len(selected) != len(purchase_ids):
            raise RepairError("Connection or selection changed since the authoritative scan.")
        plan_id, expires = uuid.uuid4().hex, now() + timedelta(minutes=10)
        operations = []
        for identifier in sorted(purchase_ids):
            item = selected[identifier]
            if item.disposition != "eligible" or not item.state or decide(item.state, policy, manifest.identity, now()).disposition != "eligible":
                raise RepairError("A selected record is ineligible or stale. Refresh the authoritative preview.")
            state = item.state
            effect = Effect(account_id=state.account_id, feature=state.feature, period_start=state.period_start, period_end=state.period_end)
            fields = {"operation_id": uuid.uuid4().hex, "plan_id": plan_id, "identity": manifest.identity.model_dump(mode="json"),
                      "workspace_digest": self.workspace_digest, "connection_digest": digest(manifest), "policy": policy.model_dump(mode="json"),
                      "policy_digest": digest(policy), "purchase_id": identifier, "payment_id": state.payment_id,
                      "expected_revision": state.revision, "effect": effect.model_dump(mode="json"),
                      "logical_effect": digest([manifest.identity.application, manifest.identity.tenant, manifest.identity.environment, identifier, state.payment_id, effect.model_dump(mode="json")]),
                      "deadline": expires.isoformat()}
            unsealed = ConditionalOperation.model_construct(**{**fields, "identity": manifest.identity, "policy": policy,
                "effect": effect, "deadline": expires, "payload_digest": "0" * 64})
            operations.append(ConditionalOperation.model_validate(unsealed.model_copy(update={"payload_digest": digest(unsealed, exclude={"payload_digest"})}).model_dump(mode="json")))
        fields = {"plan_id": plan_id, "identity": manifest.identity.model_dump(mode="json"), "connection_digest": digest(manifest),
                  "workspace_digest": self.workspace_digest, "policy_digest": digest(policy), "expires_at": expires.isoformat(),
                  "operations": [item.model_dump(mode="json") for item in operations], "maximum_writes": len(operations)}
        unsealed = TargetPlan.model_construct(**{**fields, "identity": manifest.identity, "operations": operations,
                                                 "expires_at": expires, "digest": "0" * 64})
        plan = TargetPlan.model_validate(unsealed.model_copy(update={"digest": digest(unsealed, exclude={"digest"})}).model_dump(mode="json"))
        self.store.create_run_with_tasks(RunRecord(run_id=plan_id, original_task="Approved customer purchase-access correction", workflow_type="customer_repair", model="deterministic-policy", workspace=self.workspace),
                                         [TaskSpec(task_id="customer-repair", title="Conditional customer correction", description="Apply exact plan and independently verify normal access.", assigned_role="operator", maximum_attempts=1, done_criteria=["Durable target receipts and normal access verification."])])
        self.store.update_run_status(plan_id, RunStatus.RUNNING)
        self.store.update_run_status(plan_id, RunStatus.WAITING_FOR_APPROVAL)
        self.store.update_task_status(plan_id, "customer-repair", TaskStatus.READY)
        with self.base._connection(write=True) as connection:
            registered = connection.execute("SELECT manifest FROM repair_connections WHERE connection_id=?", (manifest.connection_id,)).fetchone()
            fence = connection.execute("SELECT fenced FROM repair_connection_fences WHERE connection_id=?", (manifest.connection_id,)).fetchone()
            if not registered or registered[0] != manifest.model_dump_json() or (fence and fence[0]):
                raise RepairError("Connection changed while preparing the plan. Refresh after reconfiguration finishes.")
            connection.execute("INSERT INTO repair_live_plans VALUES (?,?,?,?,?,?,?,NULL,0,NULL,NULL)",
                               (plan_id, case_id, scan_id, manifest.model_dump_json(), credential_fingerprint(manifest), plan.model_dump_json(), "review"))
            connection.executemany("INSERT INTO repair_remote_operations(operation_id,plan_id,payload,execution,verification,attempts) VALUES (?,?,?,'queued','not_started',0)",
                                   [(item.operation_id, plan_id, item.model_dump_json()) for item in operations])
        return self.plan(plan_id)

    def plan(self, plan_id):
        self._ensure()
        with self.base._connection() as connection:
            row = connection.execute("SELECT * FROM repair_live_plans WHERE plan_id=?", (plan_id,)).fetchone()
            if row is None:
                raise RepairError("Connected correction plan was not found.", status=404)
            outcomes = [dict(item) for item in connection.execute("SELECT operation_id,execution,verification,attempts,receipt,verification_evidence FROM repair_remote_operations WHERE plan_id=? ORDER BY operation_id", (plan_id,))]
            errors = {row["operation_id"]: {"code": row["code"], "at": row["at"]} for row in connection.execute("SELECT e.* FROM repair_verification_errors e JOIN repair_remote_operations o ON o.operation_id=e.operation_id WHERE o.plan_id=?", (plan_id,))}
        for item in outcomes:
            item["verification_error"] = errors.get(item["operation_id"])
            for name in ("receipt", "verification_evidence"):
                item[name] = json.loads(item[name]) if item[name] else None
        counts = Counter(item["execution"] for item in outcomes)
        counts.update({"verified": sum(item["verification"] == "passed" for item in outcomes), "verification_pending": sum(item["verification"] == "pending" for item in outcomes),
                       "verification_failed": sum(item["verification"] == "failed" for item in outcomes), "superseded": sum(item["verification"] == "superseded" for item in outcomes)})
        counts["conflicts"] = sum(bool(item["receipt"] and item["receipt"]["reason"] == "conflict") for item in outcomes)
        counts["skipped"] = sum(item["execution"] == "not_applied" and item["receipt"]["reason"] != "conflict" for item in outcomes)
        return {"plan": json.loads(row["payload"]), "case_id": row["case_id"], "status": row["status"], "stopped": bool(row["stopped"]),
                "approved": row["approved_at"] is not None, "counts": dict(counts), "outcomes": outcomes}

    def approve(self, plan_id, plan_digest):
        result = self.plan(plan_id)
        plan = TargetPlan.model_validate(result["plan"])
        if plan.digest != plan_digest or result["stopped"] or plan.workspace_digest != self.workspace_digest:
            raise RepairError("The exact plan scope changed or was stopped.", status=403)
        with self.base._connection() as connection:
            row = connection.execute("SELECT * FROM repair_live_plans WHERE plan_id=?", (plan_id,)).fetchone()
        manifest = ConnectionManifest.model_validate_json(row["manifest"])
        self._validate_current(row, plan, manifest)
        if not result["approved"]:
            approval.approve(plan, self.workspace, manifest.destination, self.store)
            with self.base._connection(write=True) as connection:
                connection.execute("UPDATE repair_live_plans SET approved_at=?,status='approved' WHERE plan_id=? AND stopped=0", (now().isoformat(), plan_id))
        approval.validate(plan, self.workspace, manifest.destination, self.store)
        # Safe to repeat registration with the identical immutable plan after a lost response.
        self.connector_factory(manifest).approve(plan)
        return self.plan(plan_id)

    def _validate_current(self, row, plan, manifest):
        if (plan.workspace_digest != self.workspace_digest or now() >= plan.expires_at or
            self.manifest(manifest.connection_id) != manifest or
            credential_fingerprint(manifest) != row["credential_fingerprint"]):
            raise RepairError("Connection, credential, workspace or approval deadline changed. Re-review a fresh plan.", status=403)
        with self.base._connection() as connection:
            attached = connection.execute("SELECT scan_id FROM repair_attached WHERE case_id=?", (row["case_id"],)).fetchone()
        if not attached or attached[0] != row["scan_id"]:
            raise RepairError("A newer authoritative preview invalidated this plan.", status=403)
        self._ready(manifest.connection_id, writing=True, policy=plan.operations[0].policy)

    def execute(self, plan_id, *, retry_unknown=False):
        result = self.plan(plan_id)
        plan = TargetPlan.model_validate(result["plan"])
        owner = uuid.uuid4().hex
        with self.base._connection(write=True) as connection:
            row = connection.execute("SELECT * FROM repair_live_plans WHERE plan_id=?", (plan_id,)).fetchone()
            if row["lease_until"] and row["lease_until"] > now().isoformat():
                raise RepairError("Another worker is reconciling this plan. Refresh its durable results.")
            connection.execute("UPDATE repair_live_plans SET lease_owner=?,lease_until=? WHERE plan_id=?", (owner, (now() + timedelta(seconds=45)).isoformat(), plan_id))
        manifest = ConnectionManifest.model_validate_json(row["manifest"])
        connector = self.connector_factory(manifest)
        try:
            for operation in plan.operations:
                with self.base._connection() as connection:
                    state = connection.execute("SELECT * FROM repair_remote_operations WHERE operation_id=?", (operation.operation_id,)).fetchone()
                    live = connection.execute("SELECT * FROM repair_live_plans WHERE plan_id=?", (plan_id,)).fetchone()
                if live["lease_owner"] != owner:
                    raise RepairError("Worker lease changed. Existing target effects remain bound to their original operations.")
                if state["execution"] in {"dispatched", "outcome_unknown", "committed"}:
                    self._reconcile(connector, operation)
                    with self.base._connection() as connection:
                        state = connection.execute("SELECT * FROM repair_remote_operations WHERE operation_id=?", (operation.operation_id,)).fetchone()
                if state["execution"] in {"committed", "not_applied"}:
                    continue
                if live["stopped"] or not live["approved_at"]:
                    continue
                with self.base._connection() as connection:
                    paused = connection.execute("SELECT paused FROM repair_pauses WHERE plan_id=?", (plan_id,)).fetchone()
                if paused and paused[0]:
                    continue
                if state["execution"] == "outcome_unknown" and (not retry_unknown or state["attempts"] >= 3):
                    continue
                with self.base._connection() as connection:
                    retry_wait = connection.execute("SELECT next_at FROM repair_retry_wait WHERE operation_id=?", (operation.operation_id,)).fetchone()
                if retry_wait and retry_wait[0] > now().isoformat():
                    continue
                self._validate_current(live, plan, manifest)
                decision = approval.validate(plan, self.workspace, manifest.destination, self.store)
                connector.approve(plan)
                runtime_run = self.store.get_run(plan_id)
                if runtime_run.status == RunStatus.WAITING_FOR_APPROVAL:
                    self.store.update_run_status(plan_id, RunStatus.RUNNING)
                if self.store.get_task(plan_id, "customer-repair").status == TaskStatus.READY:
                    self.store.start_attempt(plan_id, "customer-repair")
                self.store.record_tool_policy_decision(plan_id, decision, approval_id="repair-" + plan_id)
                self.store.mark_tool_invocation_started(plan_id, plan_id, approval_id="repair-" + plan_id)
                # Persist immutable intent and dispatch marker before any mutation request.
                with self.base._connection(write=True) as connection:
                    changed = connection.execute("UPDATE repair_remote_operations SET execution='dispatched',attempts=attempts+1 WHERE operation_id=? AND execution IN ('queued','outcome_unknown') AND EXISTS(SELECT 1 FROM repair_live_plans WHERE plan_id=? AND stopped=0 AND lease_owner=?) AND NOT EXISTS(SELECT 1 FROM repair_pauses WHERE plan_id=? AND paused=1)", (operation.operation_id, plan_id, owner, plan_id))
                    if not changed.rowcount:
                        continue
                    connection.execute("INSERT INTO repair_dispatch_history(operation_id,attempt,at) SELECT operation_id,attempts,? FROM repair_remote_operations WHERE operation_id=?", (now().isoformat(), operation.operation_id))
                    connection.execute("UPDATE repair_live_plans SET lease_until=?,status='executing' WHERE plan_id=? AND lease_owner=?", ((now() + timedelta(seconds=45)).isoformat(), plan_id, owner))
                self.store.record_event(plan_id, "customer_repair_dispatched", {"operation_id": operation.operation_id, "payload_digest": operation.payload_digest})
                try:
                    receipt = connector.apply(operation)
                    self._save_receipt(operation, receipt)
                except ConnectorError as error:
                    with self.base._connection(write=True) as connection:
                        connection.execute("UPDATE repair_remote_operations SET execution='outcome_unknown' WHERE operation_id=? AND execution='dispatched'", (operation.operation_id,))
                        delay = max(error.retry_after or 0, random.uniform(0.25, 0.5) * 2**min(state["attempts"], 3))
                        connection.execute("INSERT INTO repair_retry_wait VALUES (?,?) ON CONFLICT(operation_id) DO UPDATE SET next_at=excluded.next_at", (operation.operation_id, (now() + timedelta(seconds=delay)).isoformat()))
                self._reconcile(connector, operation)
                if len(plan.operations) > 1:
                    time.sleep(1 / operation.policy.requests_per_second)
        finally:
            with self.base._connection(write=True) as connection:
                connection.execute("UPDATE repair_live_plans SET lease_owner=NULL,lease_until=NULL WHERE plan_id=? AND lease_owner=?", (plan_id, owner))
        self._finish(plan_id)
        return self.plan(plan_id)

    def _save_receipt(self, operation, receipt):
        if (receipt.operation_id != operation.operation_id or receipt.payload_digest != operation.payload_digest or
            receipt.logical_effect != operation.logical_effect or receipt.identity != operation.identity or
            receipt.purchase_id != operation.purchase_id or receipt.payment_id != operation.payment_id or receipt.effect != operation.effect):
            raise ConnectorError("connector_invalid")
        with self.base._connection(write=True) as connection:
            old = connection.execute("SELECT receipt FROM repair_remote_operations WHERE operation_id=?", (operation.operation_id,)).fetchone()[0]
            if old and old != receipt.model_dump_json():
                raise ConnectorError("connector_invalid")
            connection.execute("UPDATE repair_remote_operations SET execution=?,receipt=? WHERE operation_id=?", (receipt.outcome, receipt.model_dump_json(), operation.operation_id))

    def reconcile(self, plan_id):
        result = self.plan(plan_id)
        plan = TargetPlan.model_validate(result["plan"])
        if plan.workspace_digest != self.workspace_digest:
            raise RepairError("This journal belongs to a different workspace.", status=403)
        with self.base._connection() as connection:
            manifest = ConnectionManifest.model_validate_json(connection.execute("SELECT manifest FROM repair_live_plans WHERE plan_id=?", (plan_id,)).fetchone()[0])
        connector = self.connector_factory(manifest)
        for operation in plan.operations:
            if not self._reconcile(connector, operation):
                break
        self._finish(plan_id)
        return self.plan(plan_id)

    def _reconcile(self, connector, operation):
        try:
            receipt = connector.lookup(operation.operation_id)
            self._save_receipt(operation, receipt)
            if receipt.outcome == "committed":
                for poll in range(3):
                    verification = connector.verify(operation.operation_id)
                    if verification.status != "pending" or poll == 2:
                        break
                    time.sleep(random.uniform(0.05, 0.1) * 2**poll)
                if verification.operation_id != operation.operation_id:
                    raise ConnectorError("connector_invalid")
                verification = verification.model_copy(update={"reason": {
                    "passed": "The application reports that the normal authorization path provides the purchased feature.",
                    "pending": "The required authorization projection is not confirmed yet.",
                    "failed": "The normal authorization path did not confirm access.",
                    "superseded": "A legitimate later business action superseded this correction.",
                }[verification.status]})
                with self.base._connection(write=True) as connection:
                    connection.execute("UPDATE repair_remote_operations SET verification=?,verification_evidence=? WHERE operation_id=?", (verification.status, verification.model_dump_json(), operation.operation_id))
                    connection.execute("DELETE FROM repair_verification_errors WHERE operation_id=?", (operation.operation_id,))
            return True
        except ConnectorError as error:
            with self.base._connection(write=True) as connection:
                connection.execute("UPDATE repair_remote_operations SET execution='outcome_unknown' WHERE operation_id=? AND execution='dispatched'", (operation.operation_id,))
                connection.execute("UPDATE repair_remote_operations SET verification='pending' WHERE operation_id=? AND execution='committed' AND verification!='superseded'", (operation.operation_id,))
                connection.execute("INSERT INTO repair_verification_errors VALUES (?,?,?) ON CONFLICT(operation_id) DO UPDATE SET code=excluded.code,at=excluded.at", (operation.operation_id, error.code, now().isoformat()))
            return error.code == "connector_not_found"

    def _finish(self, plan_id):
        result = self.plan(plan_id)
        resolved = all((item["execution"] == "not_applied" and item["receipt"]["reason"] != "conflict") or (item["execution"] == "committed" and item["verification"] in {"passed", "superseded"}) for item in result["outcomes"])
        with self.base._connection(write=True) as connection:
            paused = connection.execute("SELECT paused FROM repair_pauses WHERE plan_id=?", (plan_id,)).fetchone()
            status = "completed" if resolved else "paused" if paused and paused[0] else "needs_attention"
            connection.execute("UPDATE repair_live_plans SET status=? WHERE plan_id=? AND stopped=0", (status, plan_id))
        run = self.store.get_run(plan_id)
        if resolved and run.status == RunStatus.RUNNING:
            self._complete_runtime_tool(plan_id, result)
            attempts = self.store.list_attempts(plan_id, "customer-repair")
            for attempt in attempts:
                if attempt.status == AttemptStatus.RUNNING:
                    try:
                        self.store.complete_attempt(attempt.attempt_id, AttemptStatus.SUCCEEDED, observation_summary="Target receipts reconciled and normal access checks passed or were legitimately superseded.")
                    except InvalidStateTransition:
                        if self.store.get_attempt(attempt.attempt_id).status not in {AttemptStatus.SUCCEEDED, AttemptStatus.INTERRUPTED}:
                            raise
            try:
                if self.store.get_task(plan_id, "customer-repair").status == TaskStatus.RUNNING:
                    self.store.update_task_status(plan_id, "customer-repair", TaskStatus.SUCCEEDED)
                if self.store.get_task(plan_id, "customer-repair").status == TaskStatus.SUCCEEDED and self.store.get_run(plan_id).status == RunStatus.RUNNING:
                    self.store.update_run_status(plan_id, RunStatus.SUCCEEDED)
            except InvalidStateTransition:
                # Another reconciler may have committed the immutable task result
                # before its separate run-status update. Observe that completion;
                # never issue a second terminal transition or rewrite the attempt.
                if (self.store.get_task(plan_id, "customer-repair").status not in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}
                    and self.store.get_run(plan_id).status not in {RunStatus.SUCCEEDED, RunStatus.CANCELLED}):
                    raise

    def stop(self, plan_id):
        result = self.plan(plan_id)
        with self.base._connection(write=True) as connection:
            connection.execute("UPDATE repair_live_plans SET stopped=1,status='stopping' WHERE plan_id=?", (plan_id,))
            manifest = ConnectionManifest.model_validate_json(connection.execute("SELECT manifest FROM repair_live_plans WHERE plan_id=?", (plan_id,)).fetchone()[0])
        # Revocation fences at the target, not merely in a local mutex. Keep stopping
        # visible if the network cannot confirm the fence; reconciliation is allowed.
        self.connector_factory(manifest).revoke(plan_id)
        with self.base._connection(write=True) as connection:
            connection.execute("UPDATE repair_live_plans SET status='stopped' WHERE plan_id=?", (plan_id,))
        self._close_stopped_runtime(plan_id)
        return self.plan(plan_id)

    def _close_stopped_runtime(self, plan_id):
        if self.store.get_run(plan_id).status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return
        for attempt in self.store.list_attempts(plan_id, "customer-repair"):
            if attempt.status == AttemptStatus.RUNNING:
                try:
                    self.store.complete_attempt(attempt.attempt_id, AttemptStatus.INTERRUPTED,
                        observation_summary="New dispatch stopped. Reconcile independently committed target operations; cancellation does not undo them.")
                except InvalidStateTransition:
                    if self.store.get_attempt(attempt.attempt_id).status not in {AttemptStatus.SUCCEEDED, AttemptStatus.INTERRUPTED}:
                        raise
        try:
            if self.store.get_task(plan_id, "customer-repair").status not in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
                self.store.update_task_status(plan_id, "customer-repair", TaskStatus.CANCELLED)
            if self.store.get_task(plan_id, "customer-repair").status == TaskStatus.CANCELLED and self.store.get_run(plan_id).status not in {RunStatus.SUCCEEDED, RunStatus.CANCELLED}:
                self.store.update_run_status(plan_id, RunStatus.CANCELLED)
        except InvalidStateTransition:
            if self.store.get_task(plan_id, "customer-repair").status not in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
                raise

    def _complete_runtime_tool(self, plan_id, result):
        from agentbus.execution.state_store import ToolInvocationConflictError
        from agentbus.tools.protocol import ToolInvocationStatus, ToolResourceUsage, ToolResult
        from agentbus.tools.records import build_tool_audit_record
        record = self.store.get_tool_invocation(plan_id, plan_id)
        if record.status not in {ToolInvocationStatus.RUNNING, ToolInvocationStatus.SUCCEEDED}:
            return
        plan = TargetPlan.model_validate(result["plan"])
        with self.base._connection() as connection:
            manifest = ConnectionManifest.model_validate_json(connection.execute("SELECT manifest FROM repair_live_plans WHERE plan_id=?", (plan_id,)).fetchone()[0])
        invocation, _descriptor = approval.invocation_for(plan, self.workspace, manifest.destination)
        completed = now()
        duration = max(0, (completed - record.started_at).total_seconds())
        tool_result = ToolResult(invocation_id=plan_id, invocation_revision=1, status=ToolInvocationStatus.SUCCEEDED,
            policy_decision=record.policy_decision, approval_id="repair-" + plan_id,
            structured_output={"counts": result["counts"], "verification": "normal_authorization_path"},
            duration_seconds=duration, resource_usage=ToolResourceUsage(wall_clock_seconds=duration))
        if record.status == ToolInvocationStatus.RUNNING:
            try:
                self.store.complete_tool_invocation(plan_id, tool_result, completed_at=completed)
            except ToolInvocationConflictError:
                if self.store.get_tool_invocation(plan_id, plan_id).status != ToolInvocationStatus.SUCCEEDED:
                    raise
            record = self.store.get_tool_invocation(plan_id, plan_id)
        audit = build_tool_audit_record(invocation, record.safe_result, started_at=record.started_at, completed_at=record.completed_at,
            audit_id="repair-audit-" + plan_id,
            affected_resource_hashes={item["operation_id"]: digest(item["receipt"]) for item in result["outcomes"] if item["receipt"]}, created_at=record.completed_at)
        self.store.record_tool_audit(audit)

    def pause(self, plan_id, *, resume=False):
        result = self.plan(plan_id)
        if result["stopped"]:
            raise RepairError("An emergency-stopped plan cannot be revived. Reconcile its receipts and review new work.")
        with self.base._connection(write=True) as connection:
            connection.execute("INSERT INTO repair_pauses VALUES (?,?) ON CONFLICT(plan_id) DO UPDATE SET paused=excluded.paused", (plan_id, int(not resume)))
            connection.execute("UPDATE repair_live_plans SET status=? WHERE plan_id=?", ("approved" if resume else "paused", plan_id))
        return self.execute(plan_id) if resume else self.plan(plan_id)

    def plans(self, case_id):
        self._ensure()
        with self.base._connection() as connection:
            identifiers = [row[0] for row in connection.execute("SELECT plan_id FROM repair_live_plans WHERE case_id=? ORDER BY rowid DESC LIMIT 50", (case_id,))]
        return [self.plan(identifier) for identifier in identifiers]

    def purge_scan_evidence(self, case_id):
        """Explicit minimization, not rollback: keep plans, intents and receipts.

        Only scans without a referencing plan may be removed. Target receipts and
        the local operation journal are deliberately never purged by this command.
        """
        self._ensure()
        with self.base._connection(write=True) as connection:
            removable = [row[0] for row in connection.execute("SELECT scan_id FROM repair_scans WHERE case_id=? AND scan_id NOT IN(SELECT scan_id FROM repair_live_plans)", (case_id,))]
            count = 0
            for scan_id in removable:
                connection.execute("DELETE FROM repair_attached WHERE scan_id=?", (scan_id,))
                count += connection.execute("DELETE FROM repair_decisions WHERE scan_id=?", (scan_id,)).rowcount
                connection.execute("DELETE FROM repair_scans WHERE scan_id=?", (scan_id,))
            return {"purged_decisions": count, "preserved_operation_receipts": True}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS repair_connections(connection_id TEXT PRIMARY KEY,manifest TEXT NOT NULL,status TEXT,credential_fingerprint TEXT);
CREATE TABLE IF NOT EXISTS repair_scans(scan_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,connection_id TEXT NOT NULL,connection_digest TEXT NOT NULL,policy TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repair_attached(case_id TEXT PRIMARY KEY,scan_id TEXT NOT NULL REFERENCES repair_scans(scan_id));
CREATE TABLE IF NOT EXISTS repair_decisions(scan_id TEXT NOT NULL REFERENCES repair_scans(scan_id),purchase_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scan_id,purchase_id));
CREATE TABLE IF NOT EXISTS repair_live_plans(plan_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,scan_id TEXT NOT NULL REFERENCES repair_scans(scan_id),manifest TEXT NOT NULL,credential_fingerprint TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,approved_at TEXT,stopped INTEGER NOT NULL,lease_owner TEXT,lease_until TEXT);
CREATE INDEX IF NOT EXISTS repair_live_case_plans ON repair_live_plans(case_id);
CREATE TABLE IF NOT EXISTS repair_remote_operations(operation_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES repair_live_plans(plan_id),payload TEXT NOT NULL,execution TEXT NOT NULL,verification TEXT NOT NULL,attempts INTEGER NOT NULL,receipt TEXT,verification_evidence TEXT);
CREATE INDEX IF NOT EXISTS repair_operations_plan ON repair_remote_operations(plan_id);
CREATE TABLE IF NOT EXISTS repair_dispatch_history(sequence INTEGER PRIMARY KEY AUTOINCREMENT,operation_id TEXT NOT NULL,attempt INTEGER NOT NULL,at TEXT NOT NULL,UNIQUE(operation_id,attempt));
CREATE TABLE IF NOT EXISTS repair_retry_wait(operation_id TEXT PRIMARY KEY,next_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repair_pauses(plan_id TEXT PRIMARY KEY,paused INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS repair_connection_fences(connection_id TEXT PRIMARY KEY,fenced INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS repair_verification_errors(operation_id TEXT PRIMARY KEY,code TEXT NOT NULL,at TEXT NOT NULL);
"""
