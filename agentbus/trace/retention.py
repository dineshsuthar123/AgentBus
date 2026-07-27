from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from agentbus.trace.blobs import BlobMetadata, RetentionClass
from agentbus.trace.errors import TraceIntegrityError, TraceStorageError
from agentbus.trace.models import (
    MAX_TRACE_ITEMS,
    Sha256Digest,
    Trace,
    TraceModel,
    TraceStatus,
    utc_now,
)
from agentbus.trace.redaction import canonical_json_bytes
from agentbus.trace.storage import ContentAddressedStore

GC_JOURNAL_SCHEMA_VERSION = 1
_JOURNAL_NAME = "gc-journal.json"
_MAX_JOURNAL_BYTES = 16 * 1024 * 1024


class TraceRetentionPolicy(TraceModel):
    keep_all: bool = False
    keep_failures: bool = True
    keep_recent: int = Field(default=100, ge=0, le=MAX_TRACE_ITEMS)
    keep_referenced: bool = True
    max_age_seconds: int | None = Field(
        default=None,
        ge=1,
        le=10 * 365 * 24 * 60 * 60,
    )
    max_total_bytes: int | None = Field(default=None, ge=0)


class GarbageCollectionPlan(TraceModel):
    plan_id: str = Field(min_length=1, max_length=128)
    created_at: datetime
    policy: TraceRetentionPolicy
    scanned_objects: int = Field(ge=0)
    original_bytes: int = Field(ge=0)
    protected_hashes: list[Sha256Digest] = Field(
        default_factory=list,
        max_length=MAX_TRACE_ITEMS,
    )
    deletion_hashes: list[Sha256Digest] = Field(
        default_factory=list,
        max_length=MAX_TRACE_ITEMS,
    )
    reclaimable_bytes: int = Field(ge=0)
    target_unreachable: bool = False
    reasons: dict[Sha256Digest, str] = Field(default_factory=dict)

    @field_validator("protected_hashes")
    @classmethod
    def protected_are_sorted_unique(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("protected hashes must be sorted and unique")
        return value

    @field_validator("deletion_hashes")
    @classmethod
    def deletions_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("deletion hashes must be unique")
        return value

    @model_validator(mode="after")
    def plan_integrity_is_valid(self) -> "GarbageCollectionPlan":
        if set(self.protected_hashes) & set(self.deletion_hashes):
            raise ValueError(
                "protected objects cannot appear in the deletion plan"
            )
        if set(self.reasons) != set(self.deletion_hashes):
            raise ValueError(
                "every planned deletion must have exactly one reason"
            )
        if self.plan_id != _gc_plan_id(
            policy=self.policy,
            scanned_objects=self.scanned_objects,
            original_bytes=self.original_bytes,
            protected_hashes=self.protected_hashes,
            deletion_hashes=self.deletion_hashes,
            reclaimable_bytes=self.reclaimable_bytes,
            target_unreachable=self.target_unreachable,
            reasons=self.reasons,
        ):
            raise ValueError("garbage-collection plan integrity check failed")
        return self


class GarbageCollectionReport(TraceModel):
    plan_id: str
    scanned_objects: int = Field(ge=0)
    deleted_objects: int = Field(ge=0)
    skipped_objects: int = Field(ge=0)
    reclaimed_bytes: int = Field(ge=0)
    target_unreachable: bool = False
    resumed: bool = False


class _GarbageCollectionJournal(TraceModel):
    schema_version: int = GC_JOURNAL_SCHEMA_VERSION
    plan: GarbageCollectionPlan
    deleted_hashes: list[Sha256Digest] = Field(default_factory=list)
    skipped_hashes: list[Sha256Digest] = Field(default_factory=list)
    reclaimed_bytes: int = Field(default=0, ge=0)

    @field_validator("schema_version")
    @classmethod
    def schema_is_supported(cls, value: int) -> int:
        if value != GC_JOURNAL_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported garbage-collection journal version: {value}"
            )
        return value


class TraceRetentionManager:
    """Crash-recoverable object GC that never operates outside the trace store."""

    def __init__(
        self,
        store: ContentAddressedStore,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.clock = clock
        self.journal_path = self.store.root / _JOURNAL_NAME
        self.store._assert_safe_location(self.journal_path)

    def plan(
        self,
        traces: Iterable[Trace],
        *,
        policy: TraceRetentionPolicy | None = None,
        active_replay_trace_ids: Iterable[str] = (),
        exported_fixture_hashes: Iterable[str] = (),
    ) -> GarbageCollectionPlan:
        selected_policy = policy or TraceRetentionPolicy()
        trace_list = list(traces)
        metadata = self.store.verify_all()
        if len(metadata) > MAX_TRACE_ITEMS:
            raise TraceStorageError(
                "Trace object count exceeds the garbage-collection bound."
            )
        references = {
            trace.trace_id: _trace_hashes(trace) for trace in trace_list
        }
        protected = self._protected_hashes(
            trace_list,
            references,
            metadata,
            selected_policy,
            active_replay_trace_ids=set(active_replay_trace_ids),
            exported_fixture_hashes=set(exported_fixture_hashes),
        )
        candidates = sorted(
            (item for item in metadata if item.sha256 not in protected),
            key=lambda item: (item.created_at, item.sha256),
        )
        selected, reasons = _select_deletions(
            candidates,
            metadata,
            selected_policy,
            now=self.clock(),
        )
        reclaimable = sum(item.byte_size for item in selected)
        remaining_bytes = (
            sum(item.byte_size for item in metadata) - reclaimable
        )
        target_unreachable = (
            selected_policy.max_total_bytes is not None
            and remaining_bytes > selected_policy.max_total_bytes
        )
        protected_hashes = sorted(protected)
        deletion_hashes = [item.sha256 for item in selected]
        original_bytes = sum(item.byte_size for item in metadata)
        plan_id = _gc_plan_id(
            policy=selected_policy,
            scanned_objects=len(metadata),
            original_bytes=original_bytes,
            protected_hashes=protected_hashes,
            deletion_hashes=deletion_hashes,
            reclaimable_bytes=reclaimable,
            target_unreachable=target_unreachable,
            reasons=reasons,
        )
        return GarbageCollectionPlan(
            plan_id=plan_id,
            created_at=self.clock(),
            policy=selected_policy,
            scanned_objects=len(metadata),
            original_bytes=original_bytes,
            protected_hashes=protected_hashes,
            deletion_hashes=deletion_hashes,
            reclaimable_bytes=reclaimable,
            target_unreachable=target_unreachable,
            reasons=reasons,
        )

    def execute(
        self,
        plan: GarbageCollectionPlan,
        *,
        current_references: Callable[[], set[str]] | None = None,
        after_delete: Callable[[str, int], None] | None = None,
    ) -> GarbageCollectionReport:
        with self.store._lock:
            journal = self._load_journal()
            resumed = journal is not None
            if journal is None:
                journal = _GarbageCollectionJournal(plan=plan)
                self._write_journal(journal)
            elif journal.plan != plan:
                raise TraceStorageError(
                    "A different interrupted trace GC plan must be resumed first."
                )
            return self._execute_journal(
                journal,
                current_references=current_references,
                after_delete=after_delete,
                resumed=resumed,
            )

    def resume(
        self,
        *,
        current_references: Callable[[], set[str]] | None = None,
        after_delete: Callable[[str, int], None] | None = None,
    ) -> GarbageCollectionReport:
        with self.store._lock:
            journal = self._load_journal()
            if journal is None:
                raise TraceStorageError(
                    "No interrupted trace garbage collection is available."
                )
            return self._execute_journal(
                journal,
                current_references=current_references,
                after_delete=after_delete,
                resumed=True,
            )

    def pending_plan(self) -> GarbageCollectionPlan | None:
        journal = self._load_journal()
        return journal.plan if journal is not None else None

    def _execute_journal(
        self,
        journal: _GarbageCollectionJournal,
        *,
        current_references: Callable[[], set[str]] | None,
        after_delete: Callable[[str, int], None] | None,
        resumed: bool,
    ) -> GarbageCollectionReport:
        completed = {
            *journal.deleted_hashes,
            *journal.skipped_hashes,
        }
        protected = set(journal.plan.protected_hashes)
        for digest in journal.plan.deletion_hashes:
            if digest in completed:
                continue
            live = set(current_references()) if current_references else set()
            if digest in live:
                journal.skipped_hashes.append(digest)
                self._write_journal(journal)
                completed.add(digest)
                continue
            reclaimed = self.store.delete_unreferenced(
                digest,
                referenced_hashes=protected | live,
            )
            journal.deleted_hashes.append(digest)
            journal.reclaimed_bytes += reclaimed
            self._write_journal(journal)
            completed.add(digest)
            if after_delete is not None:
                after_delete(digest, len(journal.deleted_hashes))
        self._remove_journal()
        return GarbageCollectionReport(
            plan_id=journal.plan.plan_id,
            scanned_objects=journal.plan.scanned_objects,
            deleted_objects=len(journal.deleted_hashes),
            skipped_objects=len(journal.skipped_hashes),
            reclaimed_bytes=journal.reclaimed_bytes,
            target_unreachable=journal.plan.target_unreachable,
            resumed=resumed,
        )

    def _protected_hashes(
        self,
        traces: list[Trace],
        references: dict[str, set[str]],
        metadata: list[BlobMetadata],
        policy: TraceRetentionPolicy,
        *,
        active_replay_trace_ids: set[str],
        exported_fixture_hashes: set[str],
    ) -> set[str]:
        all_hashes = {item.sha256 for item in metadata}
        protected = {
            item.sha256
            for item in metadata
            if set(item.retention_classes)
            & {RetentionClass.FIXTURE, RetentionClass.PINNED}
        }
        protected.update(exported_fixture_hashes & all_hashes)
        if policy.keep_all:
            return all_hashes
        selected_trace_ids = {
            trace.trace_id
            for trace in traces
            if trace.status == TraceStatus.RUNNING
            or trace.trace_id in active_replay_trace_ids
        }
        if policy.keep_referenced:
            selected_trace_ids.update(references)
        if policy.keep_failures:
            selected_trace_ids.update(
                trace.trace_id
                for trace in traces
                if trace.status
                in {
                    TraceStatus.FAILED,
                    TraceStatus.CANCELLED,
                    TraceStatus.INTERRUPTED,
                }
            )
            protected.update(
                item.sha256
                for item in metadata
                if RetentionClass.FAILURE in item.retention_classes
            )
        recent = sorted(
            traces,
            key=lambda trace: (trace.created_at, trace.trace_id),
            reverse=True,
        )[: policy.keep_recent]
        selected_trace_ids.update(trace.trace_id for trace in recent)
        for trace_id in selected_trace_ids:
            protected.update(references.get(trace_id, set()))
        return protected & all_hashes

    def _load_journal(self) -> _GarbageCollectionJournal | None:
        self.store._assert_safe_location(self.journal_path)
        try:
            raw = self.journal_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise TraceStorageError(
                "Unable to read the trace GC recovery journal."
            ) from exc
        if len(raw) > _MAX_JOURNAL_BYTES:
            raise TraceIntegrityError(
                "Trace GC recovery journal exceeds its safe bound."
            )
        try:
            return _GarbageCollectionJournal.model_validate_json(raw)
        except Exception as exc:
            raise TraceIntegrityError(
                "Trace GC recovery journal is invalid."
            ) from exc

    def _write_journal(self, journal: _GarbageCollectionJournal) -> None:
        payload = canonical_json_bytes(journal.model_dump(mode="json"))
        if len(payload) > _MAX_JOURNAL_BYTES:
            raise TraceStorageError(
                "Trace GC recovery journal exceeds its configured bound."
            )
        self.store._atomic_write(self.journal_path, payload)

    def _remove_journal(self) -> None:
        self.store._assert_safe_location(self.journal_path)
        try:
            self.journal_path.unlink(missing_ok=True)
        except OSError as exc:
            raise TraceStorageError(
                "Unable to complete the trace GC recovery journal."
            ) from exc


def _trace_hashes(trace: Trace) -> set[str]:
    hashes = {
        reference.sha256
        for span in trace.spans
        for reference in (
            *span.input_references,
            *span.output_references,
        )
    }
    hashes.update(
        reference.sha256
        for checkpoint in trace.checkpoints
        for reference in checkpoint.state_references
    )
    return hashes


def _gc_plan_id(
    *,
    policy: TraceRetentionPolicy,
    scanned_objects: int,
    original_bytes: int,
    protected_hashes: list[str],
    deletion_hashes: list[str],
    reclaimable_bytes: int,
    target_unreachable: bool,
    reasons: dict[str, str],
) -> str:
    core = {
        "policy": policy.model_dump(mode="json"),
        "scanned_objects": scanned_objects,
        "original_bytes": original_bytes,
        "protected_hashes": protected_hashes,
        "deletion_hashes": deletion_hashes,
        "reclaimable_bytes": reclaimable_bytes,
        "target_unreachable": target_unreachable,
        "reasons": reasons,
    }
    return "gc-" + hashlib.sha256(
        canonical_json_bytes(core)
    ).hexdigest()[:32]


def _select_deletions(
    candidates: list[BlobMetadata],
    all_metadata: list[BlobMetadata],
    policy: TraceRetentionPolicy,
    *,
    now: datetime,
) -> tuple[list[BlobMetadata], dict[str, str]]:
    if policy.keep_all or not candidates:
        return [], {}
    selected: dict[str, BlobMetadata] = {}
    reasons: dict[str, str] = {}
    has_bound = (
        policy.max_age_seconds is not None
        or policy.max_total_bytes is not None
    )
    if policy.max_age_seconds is not None:
        cutoff = now - timedelta(seconds=policy.max_age_seconds)
        for item in candidates:
            if item.created_at < cutoff:
                selected[item.sha256] = item
                reasons[item.sha256] = "age_bound"
    if policy.max_total_bytes is not None:
        total = sum(item.byte_size for item in all_metadata)
        total -= sum(item.byte_size for item in selected.values())
        for item in candidates:
            if total <= policy.max_total_bytes:
                break
            if item.sha256 in selected:
                continue
            selected[item.sha256] = item
            reasons[item.sha256] = "size_bound"
            total -= item.byte_size
    if not has_bound:
        for item in candidates:
            selected[item.sha256] = item
            reasons[item.sha256] = "unreferenced"
    ordered = [
        item for item in candidates if item.sha256 in selected
    ]
    return ordered, reasons


__all__ = [
    "GC_JOURNAL_SCHEMA_VERSION",
    "GarbageCollectionPlan",
    "GarbageCollectionReport",
    "TraceRetentionManager",
    "TraceRetentionPolicy",
]
