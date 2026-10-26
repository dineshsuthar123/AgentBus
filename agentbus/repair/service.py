from __future__ import annotations

import hmac
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentbus._sqlite import is_sqlite_busy_error
from agentbus.repair.analysis import analyze, fingerprint, target
from agentbus.repair.models import (
    DemoProof, FixReference, PurchaseEvidence, RepairBatch, RepairCase,
    RepairCaseSummary, RepairEvent, RepairPackage, RepairPreview, RepairResult,
    RepairSnapshot,
)

BEFORE_CODE = 'def purchase_completed(purchase):\n    record_payment(purchase)\n    return {"accepted": True}\n'
AFTER_CODE = 'def purchase_completed(purchase):\n    record_payment(purchase)\n    restore_missing_access(purchase.tenant_id, purchase.account_id, purchase.entitlement)\n    return {"accepted": True}\n'


class RepairError(Exception):
    def __init__(self, message: str, *, code="repair_conflict", status=409):
        super().__init__(message)
        self.code = code
        self.status = status


class RepairService:
    """One managed database; demo effects and their receipts commit together."""

    def __init__(self, root: str | Path, *, clock=None):
        self.root = Path(root).expanduser().absolute()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._initialized = False
        self._initialization_lock = threading.Lock()

    @contextmanager
    def _connection(self, *, write=False):
        connection = None
        try:
            if self.root.is_symlink():
                raise RepairError("Customer repair storage must be an owned directory.")
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            database = self.root / "customer-repair.db"
            if database.is_symlink() or not database.resolve().is_relative_to(self.root.resolve()):
                raise RepairError("Customer repair storage has an invalid boundary.")
            connection = sqlite3.connect(str(database), timeout=2, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with self._initialization_lock:
                if not self._initialized:
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                    if version not in (0, 1):
                        raise RepairError("Customer repair storage needs a compatible Syndra version.")
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.executescript(_SCHEMA)
                    self._initialized = True
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            if is_sqlite_busy_error(exc):
                raise RepairError("Customer repair storage is busy. Check saved results before retrying an action.", code="repair_storage_busy", status=503) from None
            raise RepairError("Customer repair storage could not complete the operation.", code="repair_storage_error", status=503) from None
        except BaseException:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def create(self, snapshot: RepairSnapshot, *, demo=False) -> RepairCase:
        case_id = uuid.uuid4().hex
        now = self.clock().isoformat()
        with self._connection(write=True) as connection:
            if connection.execute("SELECT COUNT(*) FROM repair_cases").fetchone()[0] >= 200:
                raise RepairError("The local repair case limit is reached. Export existing cases and select a separate managed state directory to start more.", code="repair_case_limit")
            connection.execute(
                "INSERT INTO repair_cases VALUES (?, ?, ?, ?, ?, ?)",
                (case_id, "local_demo" if demo else "imported_snapshot", snapshot.title, now,
                 snapshot.model_dump_json(), None),
            )
            if demo:
                from agentbus.repair.demo import accept_broken, exercise_fix

                for purchase in snapshot.purchases:
                    accept_broken(connection, case_id, purchase)
                    if purchase.account_id is not None:
                        connection.execute(
                            "UPDATE repair_access SET present = ? WHERE case_id = ? AND tenant_id = ? AND account_id = ? AND entitlement = ?",
                            (purchase.access_present, case_id, *target(purchase)),
                        )
                proof = exercise_fix(connection, case_id, snapshot)
                connection.execute("UPDATE repair_cases SET proof = ? WHERE case_id = ?", (proof.model_dump_json(), case_id))
            self._event(connection, case_id, "created", len(snapshot.purchases))
        return self.get(case_id)

    def list(self, *, limit=50, offset=0) -> list[RepairCaseSummary]:
        if not 1 <= limit <= 50 or not 0 <= offset <= 10_000:
            raise RepairError("Invalid repair history bounds.", status=400)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM repair_cases ORDER BY created_at DESC, case_id LIMIT ? OFFSET ?", (limit, offset),
            ).fetchall()
            return [self._summary(connection, row) for row in rows]

    def get(self, case_id: str) -> RepairCase:
        with self._connection() as connection:
            row = self._case(connection, case_id)
            return RepairCase(summary=self._summary(connection, row), snapshot=self._snapshot(connection, row),
                              demo_proof=DemoProof.model_validate_json(row["proof"]) if row["proof"] else None)

    def preview(self, case_id: str) -> RepairPreview:
        with self._connection(write=True) as connection:
            row = self._case(connection, case_id)
            if connection.execute("SELECT COUNT(*) FROM repair_previews WHERE case_id = ?", (case_id,)).fetchone()[0] >= 50:
                raise RepairError("This case has reached its preview limit. Export it and create a new case from current evidence.")
            snapshot = self._snapshot(connection, row)
            observations, counts = analyze(snapshot)
            now = self.clock()
            values = dict(preview_id=uuid.uuid4().hex, case_id=case_id,
                          snapshot_fingerprint=fingerprint(snapshot.model_dump(mode="json")),
                          observed_at=snapshot.observed_at, created_at=now, expires_at=now + timedelta(minutes=30),
                          policy=snapshot.policy, coverage_complete=snapshot.coverage_complete,
                          counts=counts, observations=observations)
            draft = RepairPreview(fingerprint="0" * 64, **values)
            preview = draft.model_copy(update={"fingerprint": fingerprint(draft.model_dump(mode="json", exclude={"fingerprint"}))})
            connection.execute("INSERT INTO repair_previews VALUES (?, ?, ?, ?)",
                               (preview.preview_id, case_id, preview.model_dump_json(), snapshot.model_dump_json()))
            self._event(connection, case_id, "previewed", counts.repairable)
            return preview

    def latest_preview(self, case_id: str) -> RepairPreview | None:
        with self._connection() as connection:
            self._case(connection, case_id)
            row = connection.execute("SELECT payload FROM repair_previews WHERE case_id = ? ORDER BY rowid DESC LIMIT 1", (case_id,)).fetchone()
            return RepairPreview.model_validate_json(row[0]) if row else None

    def apply(self, case_id: str, *, preview_id: str, preview_fingerprint: str,
              request_id: str, purchase_ids: list[str]) -> RepairBatch:
        if not re.fullmatch(r"[a-f0-9]{32}", preview_id) or not re.fullmatch(r"[a-f0-9]{64}", preview_fingerprint):
            raise RepairError("Use a valid preview identifier and fingerprint.", status=400)
        if not purchase_ids or len(purchase_ids) > 200 or len(purchase_ids) != len(set(purchase_ids)):
            raise RepairError("Select between 1 and 200 distinct proposed corrections.", status=400)
        try:
            if uuid.UUID(request_id).hex != request_id:
                raise ValueError
        except (ValueError, AttributeError):
            raise RepairError("Use a valid request identifier.", status=400) from None
        request_fingerprint = fingerprint([preview_id, preview_fingerprint, sorted(purchase_ids)])
        with self._connection(write=True) as connection:
            row = self._case(connection, case_id)
            saved = connection.execute("SELECT * FROM repair_batches WHERE case_id = ? AND request_id = ?", (case_id, request_id)).fetchone()
            if saved:
                if not hmac.compare_digest(saved["request_fingerprint"], request_fingerprint):
                    raise RepairError("This request identifier belongs to a different selection. Read its saved result.", code="repair_request_conflict")
                return RepairBatch.model_validate_json(saved["payload"])
            if row["mode"] != "local_demo":
                raise RepairError("Imported snapshots are read-only. Export corrections for the application's own execution process.", code="repair_read_only")
            preview = self._preview(connection, case_id, preview_id)
            if not hmac.compare_digest(preview.fingerprint, preview_fingerprint):
                raise RepairError("The preview fingerprint does not match. Open a fresh preview.", code="repair_preview_changed")
            if self.clock() >= preview.expires_at:
                raise RepairError("This preview expired. Preview current records again before applying.", code="repair_preview_expired")
            selected = {item.purchase_id: item for item in preview.observations if item.disposition == "repairable"}
            if any(purchase_id not in selected for purchase_id in purchase_ids):
                raise RepairError("The selection includes a record without a proposed correction.", code="repair_selection_changed")
            if connection.execute("SELECT COUNT(*) FROM repair_batches WHERE case_id = ?", (case_id,)).fetchone()[0] >= 100:
                raise RepairError("This case has reached its correction batch limit. Export its results.")
            snapshot = self._snapshot(connection, row)
            results = []
            for purchase_id in sorted(purchase_ids):
                observations, _ = analyze(snapshot)
                observation = next(item for item in observations if item.purchase_id == purchase_id)
                before = selected[purchase_id]
                purchase = next(item for item in snapshot.purchases if item.purchase_id == purchase_id)
                operation_id = None
                if observation.disposition == "repairable":
                    if (observation.observed_revision != before.observed_revision or
                        (observation.tenant_id, observation.account_id, observation.entitlement) !=
                        (before.tenant_id, before.account_id, before.entitlement)):
                        outcome, reason = "conflict", "Evidence changed after preview. Review a fresh preview for this account."
                    else:
                        operation_id = fingerprint([case_id, *target(purchase), "restore_missing_access"])
                        cursor = connection.execute(
                            "UPDATE repair_access SET present = 1, revision = revision + 1 WHERE case_id = ? AND tenant_id = ? AND account_id = ? AND entitlement = ? AND present = 0",
                            (case_id, *target(purchase)),
                        )
                        if cursor.rowcount != 1:
                            raise RepairError("The account changed during correction. Read current results and preview again.")
                        self._after_grant(connection, case_id, purchase_id)
                        snapshot = self._snapshot(connection, row)
                        verified = next(item for item in snapshot.purchases if item.purchase_id == purchase_id)
                        if verified.access_present is not True:
                            raise RepairError("The application did not confirm access. No correction batch was committed.", code="repair_verification_failed")
                        outcome, reason = "restored", "Missing access restored and read back from the local application."
                elif observation.disposition in {"already_correct", "ineligible"}:
                    outcome, reason = observation.disposition, observation.reason
                else:
                    outcome, reason = "unresolved", observation.reason
                actual = next(item for item in snapshot.purchases if item.purchase_id == purchase_id)
                results.append(RepairResult(purchase_id=purchase_id, outcome=outcome, reason=reason,
                                            verified_access=actual.access_present, operation_id=operation_id))
            restored = sum(result.outcome == "restored" for result in results)
            unresolved = sum(result.outcome in {"unresolved", "conflict"} for result in results)
            batch = RepairBatch(request_id=request_id, case_id=case_id, preview_id=preview_id,
                                created_at=self.clock(), status="needs_attention" if unresolved else "completed",
                                selected_count=len(results), restored_count=restored,
                                skipped_count=len(results) - restored - unresolved,
                                unresolved_count=unresolved, results=results)
            connection.execute("INSERT INTO repair_batches VALUES (?, ?, ?, ?, ?)",
                               (case_id, request_id, preview_id, request_fingerprint, batch.model_dump_json()))
            self._event(connection, case_id, "applied", restored)
            return batch

    def batch(self, case_id: str, request_id: str) -> RepairBatch:
        with self._connection() as connection:
            self._case(connection, case_id)
            row = connection.execute("SELECT payload FROM repair_batches WHERE case_id = ? AND request_id = ?", (case_id, request_id)).fetchone()
            if row is None:
                raise RepairError("No saved result exists for this request. Refresh the case before making another selection.", code="repair_result_not_found", status=404)
            return RepairBatch.model_validate_json(row[0])

    def batches(self, case_id: str) -> list[RepairBatch]:
        with self._connection() as connection:
            self._case(connection, case_id)
            return [RepairBatch.model_validate_json(row[0]) for row in connection.execute(
                "SELECT payload FROM repair_batches WHERE case_id = ? ORDER BY rowid DESC LIMIT 100", (case_id,),
            )]

    def events(self, case_id: str) -> list[RepairEvent]:
        with self._connection() as connection:
            self._case(connection, case_id)
            rows = connection.execute("SELECT * FROM repair_events WHERE case_id = ? ORDER BY sequence DESC LIMIT 200", (case_id,)).fetchall()
            return [RepairEvent(sequence=row["sequence"], kind=row["kind"], created_at=row["created_at"], affected_count=row["affected_count"]) for row in rows]

    def account_access(self, case_id: str, purchase_id: str) -> dict:
        with self._connection() as connection:
            row = self._case(connection, case_id)
            if row["mode"] != "local_demo":
                raise RepairError("An imported snapshot cannot read a live account. Connect a supported application adapter first.", code="repair_read_only")
            snapshot = self._snapshot(connection, row)
            purchase = next((item for item in snapshot.purchases if item.purchase_id == purchase_id), None)
            if purchase is None:
                raise RepairError("Purchase was not found in this incident cohort.", status=404)
            allowed = purchase.access_present if purchase.mapping_verified and purchase.account_id else None
            message = ("Your workspace is unlocked." if allowed else "Workspace access is still locked.") if allowed is not None else "A verified account mapping is missing. Access cannot be determined."
            return {"purchase_id": purchase_id, "account_id": purchase.account_id, "allowed": allowed,
                    "observed_at": self.clock(), "message": message}

    def export(self, case_id: str, preview_id: str) -> RepairPackage:
        with self._connection() as connection:
            case = self._case(connection, case_id)
            preview = self._preview(connection, case_id, preview_id)
            basis = connection.execute("SELECT snapshot FROM repair_previews WHERE preview_id = ? AND case_id = ?", (preview_id, case_id)).fetchone()[0]
            batches = [RepairBatch.model_validate_json(row[0]) for row in connection.execute(
                "SELECT payload FROM repair_batches WHERE case_id = ? AND preview_id = ? ORDER BY request_id", (case_id, preview_id),
            )]
            package = RepairPackage(case_id=case_id, mode=case["mode"], snapshot=RepairSnapshot.model_validate_json(basis),
                                    preview=preview, batches=batches, checksum="0" * 64)
            return package.model_copy(update={"checksum": fingerprint(package.model_dump(mode="json", exclude={"checksum"}))})

    def demo_change(self, case_id: str, action: str) -> int:
        with self._connection(write=True) as connection:
            row = self._case(connection, case_id)
            if row["mode"] != "local_demo":
                raise RepairError("Demo changes are available only in the owned synthetic application.", code="repair_read_only")
            if action == "support_corrects_five":
                count = 0
                for number in range(61, 66):
                    cursor = connection.execute("UPDATE repair_access SET present = 1, revision = revision + 1 WHERE case_id = ? AND account_id = ? AND present = 0", (case_id, f"account-{number:03}"))
                    count += cursor.rowcount
                if count:
                    self._event(connection, case_id, "support_corrected", count)
            elif action == "refund_one":
                purchase = connection.execute("SELECT payload FROM repair_purchases WHERE case_id = ? AND purchase_id = 'purchase-001'", (case_id,)).fetchone()
                original = PurchaseEvidence.model_validate_json(purchase[0])
                count = int(original.payment_state == "paid")
                if count:
                    updated = original.model_copy(update={"payment_state": "refunded", "revision": original.revision + 1})
                    connection.execute("UPDATE repair_purchases SET payload = ? WHERE case_id = ? AND purchase_id = ?", (updated.model_dump_json(), case_id, original.purchase_id))
                if count:
                    self._event(connection, case_id, "refunded", count)
            else:
                raise RepairError("Unknown demo action.", status=400)
            return count

    def _after_grant(self, connection, case_id, purchase_id):
        """Failure-injection seam; all domain writes still belong to the transaction."""

    def _snapshot(self, connection, row) -> RepairSnapshot:
        snapshot = RepairSnapshot.model_validate_json(row["snapshot"])
        if row["mode"] != "local_demo":
            return snapshot
        access = {(item["tenant_id"], item["account_id"], item["entitlement"]): item for item in connection.execute("SELECT * FROM repair_access WHERE case_id = ?", (row["case_id"],))}
        purchases = []
        cohort = {purchase.purchase_id for purchase in snapshot.purchases}
        for item in connection.execute("SELECT payload FROM repair_purchases WHERE case_id = ? ORDER BY purchase_id", (row["case_id"],)):
            purchase = PurchaseEvidence.model_validate_json(item[0])
            if purchase.purchase_id not in cohort:
                continue
            record = access.get(target(purchase))
            purchases.append(purchase.model_copy(update={
                "access_present": None if record is None or record["present"] is None else bool(record["present"]),
                "revision": purchase.revision + (record["revision"] if record else 0),
            }))
        return snapshot.model_copy(update={"purchases": purchases, "observed_at": max(snapshot.observed_at, self.clock())})

    def _case(self, connection, case_id):
        row = connection.execute("SELECT * FROM repair_cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise RepairError("Customer repair case was not found.", code="repair_case_not_found", status=404)
        return row

    def _preview(self, connection, case_id, preview_id):
        row = connection.execute("SELECT payload FROM repair_previews WHERE case_id = ? AND preview_id = ?", (case_id, preview_id)).fetchone()
        if row is None:
            raise RepairError("Preview was not found in this case. Create a new preview.", code="repair_preview_not_found", status=404)
        return RepairPreview.model_validate_json(row[0])

    def _summary(self, connection, row):
        snapshot = RepairSnapshot.model_validate_json(row["snapshot"])
        return RepairCaseSummary(case_id=row["case_id"], title=row["title"], mode=row["mode"], created_at=row["created_at"],
                                 coverage_complete=snapshot.coverage_complete, record_count=len(snapshot.purchases),
                                 batch_count=connection.execute("SELECT COUNT(*) FROM repair_batches WHERE case_id = ?", (row["case_id"],)).fetchone()[0])

    def _event(self, connection, case_id, kind, affected_count):
        connection.execute("INSERT INTO repair_events(case_id, kind, created_at, affected_count) VALUES (?, ?, ?, ?)", (case_id, kind, self.clock().isoformat(), affected_count))


def verify_package(package: RepairPackage) -> bool:
    if not hmac.compare_digest(package.checksum, fingerprint(package.model_dump(mode="json", exclude={"checksum"}))):
        return False
    if not hmac.compare_digest(package.preview.snapshot_fingerprint, fingerprint(package.snapshot.model_dump(mode="json"))):
        return False
    if not hmac.compare_digest(package.preview.fingerprint, fingerprint(package.preview.model_dump(mode="json", exclude={"fingerprint"}))):
        return False
    observations, counts = analyze(package.snapshot)
    if observations != package.preview.observations or counts != package.preview.counts:
        return False
    if package.case_id != package.preview.case_id:
        return False
    proposed = {row.purchase_id: row for row in package.preview.observations if row.disposition == "repairable"}
    for batch in package.batches:
        results = batch.results
        restored = sum(result.outcome == "restored" for result in results)
        unresolved = sum(result.outcome in {"unresolved", "conflict"} for result in results)
        if (batch.case_id != package.case_id or batch.preview_id != package.preview.preview_id
            or len({row.purchase_id for row in results}) != len(results)
            or batch.selected_count != len(results) or batch.restored_count != restored
            or batch.unresolved_count != unresolved or batch.skipped_count != len(results) - restored - unresolved
            or batch.status != ("needs_attention" if unresolved else "completed")):
            return False
        for result in results:
            source = proposed.get(result.purchase_id)
            if source is None:
                return False
            expected = fingerprint([package.case_id, source.tenant_id, source.account_id, source.entitlement, "restore_missing_access"])
            if result.outcome == "restored":
                if result.verified_access is not True or result.operation_id != expected:
                    return False
            elif result.operation_id is not None or (result.outcome == "already_correct" and result.verified_access is not True):
                return False
    return True


_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS repair_cases(case_id TEXT PRIMARY KEY, mode TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL, snapshot TEXT NOT NULL, proof TEXT);
CREATE TABLE IF NOT EXISTS repair_purchases(case_id TEXT NOT NULL REFERENCES repair_cases(case_id), purchase_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(case_id, purchase_id));
CREATE TABLE IF NOT EXISTS repair_access(case_id TEXT NOT NULL REFERENCES repair_cases(case_id), tenant_id TEXT NOT NULL, account_id TEXT NOT NULL, entitlement TEXT NOT NULL, present INTEGER CHECK(present IN (0,1)), revision INTEGER NOT NULL, PRIMARY KEY(case_id, tenant_id, account_id, entitlement));
CREATE TABLE IF NOT EXISTS repair_previews(preview_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES repair_cases(case_id), payload TEXT NOT NULL, snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repair_batches(case_id TEXT NOT NULL REFERENCES repair_cases(case_id), request_id TEXT NOT NULL, preview_id TEXT NOT NULL REFERENCES repair_previews(preview_id), request_fingerprint TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(case_id, request_id));
CREATE TABLE IF NOT EXISTS repair_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL REFERENCES repair_cases(case_id), kind TEXT NOT NULL, created_at TEXT NOT NULL, affected_count INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS repair_history ON repair_cases(created_at DESC, case_id);
CREATE INDEX IF NOT EXISTS repair_case_previews ON repair_previews(case_id);
CREATE INDEX IF NOT EXISTS repair_case_events ON repair_events(case_id, sequence DESC);
PRAGMA user_version = 1;
COMMIT;
"""
