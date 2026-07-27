from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from pydantic import Field

from agentbus.config import AgentBusConfig
from agentbus.execution.state_store import (
    ComparisonRecordNotFoundError,
    ProvenanceRecordNotFoundError,
    StateStore,
    StateStoreError,
    TraceRecordNotFoundError,
)
from agentbus.replay.checkpoints import (
    CheckpointManager,
    ReplayIsolationManager,
)
from agentbus.replay.classification import (
    ReplayabilityClassifier,
    RunReplayability,
)
from agentbus.replay.comparison import RunComparison, compare_traces
from agentbus.replay.engine import ReplayEngine
from agentbus.replay.fixtures import (
    CapturedRegressionFixture,
    FixtureAssertionReport,
    RegressionFixtureSpec,
    capture_regression_fixture,
    evaluate_fixture_assertions,
)
from agentbus.replay.forks import ForkManager, ForkRequest, ForkResult
from agentbus.replay.inputs import ReplayInputCatalog
from agentbus.replay.session import (
    ReplayRequest,
    ReplayResult,
    ReplaySession,
    ReplaySessionStatus,
)
from agentbus.trace.archive import (
    ImportedTraceArchive,
    TraceArchiveExporter,
    TraceArchiveImporter,
    TraceArchiveManifest,
)
from agentbus.trace.errors import TraceIntegrityError, TraceStorageError
from agentbus.trace.models import (
    ReplayMode,
    Trace,
    TraceIdentifier,
    TraceModel,
    TraceStatus,
    utc_now,
)
from agentbus.trace.protocols import provenance_protocol_documents
from agentbus.trace.provenance import (
    ProvenanceManifest,
    verify_provenance_core,
)
from agentbus.trace.redaction import canonical_json_bytes
from agentbus.trace.retention import (
    GarbageCollectionPlan,
    GarbageCollectionReport,
    TraceRetentionManager,
    TraceRetentionPolicy,
)
from agentbus.trace.storage import ContentAddressedStore


class TraceVerificationReport(TraceModel):
    trace_id: TraceIdentifier
    run_id: TraceIdentifier
    provenance_root: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_count: int = Field(ge=0)
    protocol_drift: list[str] = Field(default_factory=list, max_length=256)
    valid: bool = True


class ArchiveReplayResult(TraceModel):
    imported: ImportedTraceArchive
    replay: ReplayResult
    fixture_assertions: FixtureAssertionReport | None = None


class TraceReplayService:
    """Shared, providerless trace operations for CLI and control-plane callers."""

    def __init__(
        self,
        config: AgentBusConfig,
        *,
        state_store: StateStore | None = None,
        object_store: ContentAddressedStore | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config
        self.state_store = state_store or StateStore(
            config.state_database_path
        )
        self.object_store = object_store or ContentAddressedStore(
            config.trace_store_path,
            private_roots=[config.workspace_path],
        )
        self.cancelled = cancelled

    def resolve_trace(self, identifier: str) -> Trace:
        try:
            return self.state_store.get_trace(identifier)
        except TraceRecordNotFoundError:
            return self.state_store.get_run_trace(identifier)

    def list_traces(
        self,
        *,
        status: TraceStatus | None = None,
        limit: int = 100,
    ) -> list[Trace]:
        return self.state_store.list_traces(status=status, limit=limit)

    def replayability(self, identifier: str) -> RunReplayability:
        trace = self.resolve_trace(identifier)
        available = ReplayInputCatalog(
            trace,
            self.object_store,
        ).available_hashes
        return ReplayabilityClassifier().classify_trace(
            trace,
            available_object_hashes=available,
        )

    def provenance(self, identifier: str) -> ProvenanceManifest:
        trace = self.resolve_trace(identifier)
        return self.state_store.get_provenance_manifest(trace.trace_id)

    def verify(self, identifier: str) -> TraceVerificationReport:
        trace = self.resolve_trace(identifier)
        provenance = self.state_store.get_provenance_manifest(trace.trace_id)
        verify_provenance_core(provenance, trace)
        blob_hashes = sorted(
            {
                entry.identifier
                for entry in provenance.integrity_entries
                if entry.kind == "blob"
            }
        )
        for digest in blob_hashes:
            self.object_store.verify(digest)
        current_protocols = provenance_protocol_documents()
        drift = []
        for name, expected in sorted(provenance.protocol_hashes.items()):
            document = current_protocols.get(name)
            actual = (
                hashlib.sha256(canonical_json_bytes(document)).hexdigest()
                if document is not None
                else None
            )
            if actual != expected:
                drift.append(name)
        return TraceVerificationReport(
            trace_id=trace.trace_id,
            run_id=trace.run_id,
            provenance_root=provenance.integrity_root,
            object_count=len(blob_hashes),
            protocol_drift=drift,
        )

    def export_trace(
        self,
        identifier: str,
        destination: str | Path,
        *,
        include_source_content: bool = False,
    ) -> TraceArchiveManifest:
        trace = self.resolve_trace(identifier)
        provenance = self.state_store.get_provenance_manifest(trace.trace_id)
        return TraceArchiveExporter(self.object_store).export(
            trace,
            provenance,
            destination,
            include_source_content=include_source_content,
        )

    def capture_fixture(
        self,
        identifier: str,
        destination: str | Path,
        *,
        include_source_content: bool = False,
    ) -> CapturedRegressionFixture:
        trace = self.resolve_trace(identifier)
        provenance = self.state_store.get_provenance_manifest(trace.trace_id)
        return capture_regression_fixture(
            trace,
            provenance,
            self.object_store,
            destination,
            include_source_content=include_source_content,
        )

    def import_archive(
        self,
        source: str | Path,
        *,
        allow_source_content: bool = False,
    ) -> ImportedTraceArchive:
        return TraceArchiveImporter(self.object_store).import_archive(
            source,
            allow_source_content=allow_source_content,
        )

    def replay(
        self,
        identifier: str,
        request: ReplayRequest,
    ) -> ReplayResult:
        trace = self.resolve_trace(identifier)
        if (
            request.source_trace_id != trace.trace_id
            or request.source_run_id != trace.run_id
        ):
            raise StateStoreError(
                "Replay request does not match the selected trace."
            )
        engine = self._engine(trace, request)
        pending = self.state_store.create_replay_session(request)
        try:
            result = engine.replay(
                trace,
                request,
                session_created_at=pending.created_at,
            )
        except Exception as exc:
            started_at = max(pending.created_at, utc_now())
            failed = ReplaySession.model_validate(
                pending.model_copy(
                    update={
                        "status": ReplaySessionStatus.FAILED,
                        "started_at": started_at,
                        "completed_at": max(started_at, utc_now()),
                        "failure_category": type(exc).__name__,
                        "failure_message": str(exc),
                    }
                ).model_dump()
            )
            self.state_store.record_replay_session(request, failed)
            raise
        self.state_store.record_replay_session(
            request,
            result.session,
            result=result,
        )
        return result

    def replay_archive(
        self,
        source: str | Path,
        *,
        mode: ReplayMode = ReplayMode.OFFLINE,
        allow_source_content: bool = False,
    ) -> ArchiveReplayResult:
        imported = self.import_archive(
            source,
            allow_source_content=allow_source_content,
        )
        request = ReplayRequest(
            source_trace_id=imported.trace.trace_id,
            source_run_id=imported.trace.run_id,
            mode=mode,
        )
        replay = ReplayEngine(
            self.object_store,
            cancelled=self.cancelled,
        ).replay(imported.trace, request)
        fixture_report = None
        try:
            fixture = RegressionFixtureSpec.model_validate(
                imported.assertions
            )
        except Exception:
            fixture = None
        if fixture is not None:
            if (
                fixture.trace_id != imported.trace.trace_id
                or fixture.run_id != imported.trace.run_id
                or fixture.provenance_root
                != imported.provenance.integrity_root
            ):
                raise TraceIntegrityError(
                    "Regression fixture identities do not match its trace."
                )
            fixture_report = evaluate_fixture_assertions(
                fixture.assertions,
                imported.trace,
                self.object_store,
                replay,
            )
        return ArchiveReplayResult(
            imported=imported,
            replay=replay,
            fixture_assertions=fixture_report,
        )

    def fork(
        self,
        identifier: str,
        request: ForkRequest,
    ) -> ForkResult:
        trace = self.resolve_trace(identifier)
        engine = ReplayEngine(
            self.object_store,
            cancelled=self.cancelled,
        )
        result = ForkManager(self.object_store, engine).fork(trace, request)
        persisted_request = ReplayRequest(
            replay_id=request.replay_id,
            source_trace_id=request.source_trace_id,
            source_run_id=request.source_run_id,
            mode=request.mode,
            fork=True,
            changed_inputs=request.changed_inputs,
            live_provider_consent=request.live_provider_consent,
        )
        self.state_store.record_replay_session(
            persisted_request,
            result.replay.session,
            result=result.replay,
        )
        return result

    def compare(
        self,
        left_identifier: str,
        right_identifier: str,
    ) -> RunComparison:
        left = self.resolve_trace(left_identifier)
        right = self.resolve_trace(right_identifier)
        comparison = compare_traces(
            left,
            right,
            left_provenance=self._find_provenance(left.trace_id),
            right_provenance=self._find_provenance(right.trace_id),
        )
        try:
            return self.state_store.get_trace_comparison(
                comparison.comparison_id
            )
        except ComparisonRecordNotFoundError:
            return self.state_store.record_trace_comparison(comparison)

    def plan_gc(
        self,
        policy: TraceRetentionPolicy,
    ) -> GarbageCollectionPlan:
        traces = self._bounded_all_traces()
        active = self._active_replay_trace_ids()
        return TraceRetentionManager(self.object_store).plan(
            traces,
            policy=policy,
            active_replay_trace_ids=active,
        )

    def execute_gc(
        self,
        plan: GarbageCollectionPlan,
    ) -> GarbageCollectionReport:
        return TraceRetentionManager(self.object_store).execute(
            plan,
            current_references=lambda: self._current_gc_protected_hashes(
                plan.policy
            ),
        )

    def resume_gc(self) -> GarbageCollectionReport:
        manager = TraceRetentionManager(self.object_store)
        pending = manager.pending_plan()
        if pending is None:
            raise TraceStorageError(
                "No interrupted trace garbage collection is available."
            )
        return manager.resume(
            current_references=lambda: self._current_gc_protected_hashes(
                pending.policy
            ),
        )

    def _engine(
        self,
        trace: Trace,
        request: ReplayRequest,
    ) -> ReplayEngine:
        checkpoint_manager = None
        isolation_manager = None
        source_workspace = None
        if (
            request.from_checkpoint_id is not None
            or request.from_span_id is not None
        ):
            run = self.state_store.get_run(trace.run_id)
            source_workspace = Path(run.workspace).expanduser().resolve()
            replay_root = (
                source_workspace.parent
                / ".agentbus-replays"
                / source_workspace.name
            )
            request.isolated_workspace = str(
                replay_root / request.replay_id
            )
            checkpoint_manager = CheckpointManager(self.object_store)
            isolation_manager = ReplayIsolationManager(
                replay_root,
                self.state_store,
                repository_root=source_workspace,
            )
        return ReplayEngine(
            self.object_store,
            checkpoint_manager=checkpoint_manager,
            isolation_manager=isolation_manager,
            source_workspace=source_workspace,
            cancelled=self.cancelled,
        )

    def _find_provenance(
        self,
        trace_id: str,
    ) -> ProvenanceManifest | None:
        try:
            return self.state_store.get_provenance_manifest(trace_id)
        except ProvenanceRecordNotFoundError:
            return None

    def _bounded_all_traces(self) -> list[Trace]:
        count = self.state_store.count_traces()
        if count > 1_000:
            raise TraceStorageError(
                "Trace GC refused because more than 1000 traces require "
                "a paginated retention pass."
            )
        return self.state_store.list_traces(limit=max(count, 1))

    def _active_replay_trace_ids(self) -> set[str]:
        active: set[str] = set()
        for status in (
            ReplaySessionStatus.PENDING,
            ReplaySessionStatus.RUNNING,
        ):
            sessions = self.state_store.list_replay_sessions(
                status=status,
                limit=1_000,
            )
            if len(sessions) == 1_000:
                raise TraceStorageError(
                    "Trace GC refused because the active replay set exceeds "
                    "its safe bound."
                )
            active.update(
                session.source_trace_id for session in sessions
            )
        return active

    def _current_gc_protected_hashes(
        self,
        policy: TraceRetentionPolicy,
    ) -> set[str]:
        current = TraceRetentionManager(self.object_store).plan(
            self._bounded_all_traces(),
            policy=policy,
            active_replay_trace_ids=self._active_replay_trace_ids(),
        )
        return set(current.protected_hashes)


__all__ = [
    "ArchiveReplayResult",
    "TraceReplayService",
    "TraceVerificationReport",
]
