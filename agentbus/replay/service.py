from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from agentbus.config import AgentBusConfig
from agentbus.execution.models import RunRecord, RunStatus
from agentbus.execution.state_store import (
    ComparisonRecordNotFoundError,
    ProvenanceRecordNotFoundError,
    ReplaySessionNotFoundError,
    RunNotFoundError,
    StateStore,
    StateStoreError,
    TraceRecordNotFoundError,
)
from agentbus.policy.defaults import DEFAULT_TOOL_POLICY
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
from agentbus.replay.errors import ReplayIncompatibleError
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
from agentbus.replay.tools import (
    TOOL_ENVELOPE_MEDIA_TYPE,
    CapturedToolEnvelope,
    ToolReplayAssessment,
    ToolReplayPlanner,
    load_tool_envelope,
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
    TraceOutput,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
    utc_now,
)
from agentbus.trace.protocols import provenance_protocol_documents
from agentbus.trace.provenance import (
    ProvenanceBuilder,
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
from agentbus.trace.intelligence import (
    RepositoryIntelligenceTraceEvidence,
    build_repository_intelligence_trace_evidence,
)
from agentbus.tools.descriptors import descriptor_map
from agentbus.tools.protocol import (
    ToolDescriptor,
    safe_protocol_dict,
)

if TYPE_CHECKING:
    from agentbus.runtime.intelligence import PlannerIntelligenceSource


_REPLAY_PROCESS_EXECUTABLES = ("git", "pytest", "python")


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
        tool_replay_planner: ToolReplayPlanner | None = None,
        tool_descriptors: Mapping[str, ToolDescriptor] | None = None,
        intelligence_source: PlannerIntelligenceSource | None = None,
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
        self.tool_replay_planner = tool_replay_planner or ToolReplayPlanner()
        self.intelligence_source = intelligence_source
        current_descriptors = (
            dict(tool_descriptors)
            if tool_descriptors is not None
            else descriptor_map(
                workspace=config.workspace_path,
                process_executables=_REPLAY_PROCESS_EXECUTABLES,
            )
        )
        self.tool_descriptors = _validated_tool_descriptors(
            current_descriptors
        )

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
        imported = TraceArchiveImporter(self.object_store).import_archive(
            source,
            allow_source_content=allow_source_content,
        )
        self._catalog_imported_trace(imported)
        return imported

    def replay(
        self,
        identifier: str,
        request: ReplayRequest,
    ) -> ReplayResult:
        trace = self.resolve_trace(identifier)
        prepared = self._prepared_request(trace, request)
        engine = self._engine(trace, prepared)
        pending = self._pending_session(prepared)
        try:
            result = engine.replay(
                trace,
                prepared,
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
            self.state_store.record_replay_session(prepared, failed)
            raise
        self.state_store.record_replay_session(
            prepared,
            result.session,
            result=result,
        )
        return result

    def queue_replay(
        self,
        identifier: str,
        request: ReplayRequest,
    ) -> tuple[ReplayRequest, ReplaySession]:
        trace = self.resolve_trace(identifier)
        prepared = self._prepared_request(trace, request)
        return prepared, self._pending_session(prepared)

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
        try:
            fixture = RegressionFixtureSpec.model_validate(
                imported.assertions
            )
        except Exception:
            fixture = None
        if fixture is not None and (
            fixture.trace_id != imported.trace.trace_id
            or fixture.run_id != imported.trace.run_id
            or fixture.provenance_root
            != imported.provenance.integrity_root
        ):
            raise TraceIntegrityError(
                "Regression fixture identities do not match its trace."
            )
        request = ReplayRequest(
            source_trace_id=imported.trace.trace_id,
            source_run_id=imported.trace.run_id,
            mode=mode,
        )
        replay = self._engine(imported.trace, request).replay(
            imported.trace,
            request,
        )
        fixture_report = None
        if fixture is not None:
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
        persisted_request = ReplayRequest(
            replay_id=request.replay_id,
            source_trace_id=request.source_trace_id,
            source_run_id=request.source_run_id,
            mode=request.mode,
            fork=True,
            changed_inputs=request.changed_inputs,
            live_provider_consent=request.live_provider_consent,
        )
        engine = self._engine(trace, persisted_request)
        pending = self._pending_session(persisted_request)
        try:
            result = ForkManager(
                self.object_store,
                engine,
                clock=lambda: pending.created_at,
            ).fork(
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
            self.state_store.record_replay_session(
                persisted_request,
                failed,
            )
            raise
        source_manifest = self.state_store.get_provenance_manifest(
            trace.trace_id
        )
        fork_manifest = self._fork_provenance(
            result,
            source_manifest,
            generated_at=pending.created_at,
        )
        comparison = compare_traces(
            trace,
            result.fork_trace,
            left_provenance=source_manifest,
            right_provenance=fork_manifest,
            clock=lambda: pending.created_at,
        )
        result = ForkResult.model_validate(
            result.model_copy(
                update={"comparison": comparison}
            ).model_dump()
        )
        terminal_session = ReplaySession.model_validate(
            result.replay.session.model_copy(
                update={
                    "result_trace_id": result.fork_trace.trace_id,
                    "comparison_id": result.comparison.comparison_id,
                }
            ).model_dump()
        )
        terminal_replay = ReplayResult.model_validate(
            result.replay.model_copy(
                update={"session": terminal_session}
            ).model_dump()
        )
        result = ForkResult.model_validate(
            result.model_copy(
                update={"replay": terminal_replay}
            ).model_dump()
        )
        self.state_store.record_fork_trace(
            self._fork_run_record(result, fork_manifest),
            result.fork_trace,
            fork_manifest,
            result.comparison,
            source_trace_id=trace.trace_id,
            replay_id=request.replay_id,
            changed_input_names=result.changed_input_names,
        )
        self.state_store.record_replay_session(
            persisted_request,
            result.replay.session,
            result=result.replay,
        )
        return result

    def _fork_provenance(
        self,
        result: ForkResult,
        source: ProvenanceManifest,
        *,
        generated_at: datetime,
    ) -> ProvenanceManifest:
        classification = ReplayabilityClassifier().classify_trace(
            result.fork_trace,
            available_object_hashes=ReplayInputCatalog(
                result.fork_trace,
                self.object_store,
            ).available_hashes,
        )
        return ProvenanceBuilder(clock=lambda: generated_at).build(
            result.fork_trace,
            configuration={
                "mode": "providerless_fork",
                "source_configuration_fingerprint": (
                    source.configuration_fingerprint
                ),
                "changed_inputs_sha256": result.changed_inputs_sha256,
            },
            provider_routes=source.provider_routes,
            tool_descriptors=source.tool_descriptors,
            policy_version="agentbus.replay.captured-policy.v1",
            policy_document={
                "source_policy_version": source.policy_version,
                "source_policy_sha256": source.policy_sha256,
                "current_default_policy_sha256": hashlib.sha256(
                    canonical_json_bytes(
                        DEFAULT_TOOL_POLICY.model_dump(mode="json")
                    )
                ).hexdigest(),
            },
            protocol_hashes=source.protocol_hashes,
            task_graph={
                "source_task_graph_sha256": source.task_graph_sha256,
                "changed_inputs_sha256": result.changed_inputs_sha256,
                "changed_input_names": result.changed_input_names,
            },
            final_repository_tree_sha256=(
                source.final_repository_tree_sha256
            ),
            replayability=classification.level,
            replayability_reasons=classification.reasons,
        )

    @staticmethod
    def _fork_run_record(
        result: ForkResult,
        manifest: ProvenanceManifest,
    ) -> RunRecord:
        trace = result.fork_trace
        return RunRecord(
            run_id=trace.run_id,
            original_task=(
                "Providerless replay fork; original task text omitted."
            ),
            workflow_type="replay-fork",
            status=RunStatus.SUCCEEDED,
            model="captured-offline",
            workspace="[FORK_REPLAY_WORKSPACE]",
            created_at=trace.created_at,
            updated_at=trace.completed_at or trace.created_at,
            completed_at=trace.completed_at,
            graph_data={
                "version": 1,
                "fork_task_graph_sha256": manifest.task_graph_sha256,
                "tasks": [],
            },
            metadata={
                "forked_trace": True,
                "source_trace_id": result.source_trace_id,
                "replay_id": result.replay.session.replay_id,
                "trace_id": trace.trace_id,
                "comparison_id": result.comparison.comparison_id,
                "changed_input_names": result.changed_input_names,
                "changed_inputs_sha256": result.changed_inputs_sha256,
                "provenance_root": manifest.integrity_root,
                "provider_calls": result.replay.session.provider_calls,
                "network_calls": result.replay.session.network_calls,
            },
            verifier_status=(
                "passed"
                if result.replay.verifier_result is not None
                and result.replay.verifier_result.get("passed") is True
                else None
            ),
            reviewer_status=(
                "approved"
                if result.replay.reviewer_result is not None
                and result.replay.reviewer_result.get("approved") is True
                else None
            ),
        )

    def _pending_session(self, request: ReplayRequest) -> ReplaySession:
        try:
            pending = self.state_store.get_replay_session(request.replay_id)
        except ReplaySessionNotFoundError:
            return self.state_store.create_replay_session(request)
        persisted_request = self.state_store.get_replay_request(
            request.replay_id
        )
        persisted_identity = persisted_request.model_dump(
            exclude={"isolated_workspace"}
        )
        request_identity = request.model_dump(exclude={"isolated_workspace"})
        isolation_matches = (
            persisted_request.isolated_workspace is None
        ) == (request.isolated_workspace is None)
        if persisted_identity != request_identity or not isolation_matches:
            raise StateStoreError(
                f"Replay request '{request.replay_id}' is immutable."
            )
        if pending.status != ReplaySessionStatus.PENDING:
            raise StateStoreError(
                f"Replay session '{request.replay_id}' is already "
                f"{pending.status.value}."
            )
        return pending

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
            if request.isolated_workspace is None:
                raise StateStoreError(
                    "Partial replay request was not prepared for isolation."
                )
            checkpoint_manager = CheckpointManager(self.object_store)
            isolation_manager = ReplayIsolationManager(
                replay_root,
                self.state_store,
                repository_root=source_workspace,
            )
        policy_context = _ReplayToolPolicyContext(
            trace,
            self.object_store,
            _CachedToolReplayPlanner(self.tool_replay_planner),
            self.tool_descriptors,
            request=request,
        )
        return ReplayEngine(
            self.object_store,
            policy_evaluator=policy_context.evaluate,
            tool_replay_planner=policy_context.planner,
            tool_descriptors=self.tool_descriptors,
            checkpoint_manager=checkpoint_manager,
            isolation_manager=isolation_manager,
            source_workspace=source_workspace,
            repository_intelligence_resolver=(
                self._current_repository_intelligence
                if self.intelligence_source is not None
                else None
            ),
            cancelled=self.cancelled,
        )

    def _current_repository_intelligence(
        self,
        captured: RepositoryIntelligenceTraceEvidence,
    ) -> RepositoryIntelligenceTraceEvidence | None:
        if self.intelligence_source is None:
            return None
        context = self.intelligence_source.planner_context(captured.search_query)
        if context is None:
            return None
        return build_repository_intelligence_trace_evidence(
            captured.search_query,
            context,
            private_roots=(self.config.workspace_path,),
        )

    def _prepared_request(
        self,
        trace: Trace,
        request: ReplayRequest,
    ) -> ReplayRequest:
        if (
            request.source_trace_id != trace.trace_id
            or request.source_run_id != trace.run_id
        ):
            raise StateStoreError(
                "Replay request does not match the selected trace."
            )
        prepared = request.model_copy(deep=True)
        if (
            prepared.from_checkpoint_id is not None
            or prepared.from_span_id is not None
        ):
            run = self.state_store.get_run(trace.run_id)
            if run.metadata.get("imported_trace") is True:
                raise StateStoreError(
                    "Partial replay of an imported trace requires a "
                    "reconstructed repository and is not available."
                )
            source_workspace = Path(run.workspace).expanduser().resolve()
            replay_root = (
                source_workspace.parent
                / ".agentbus-replays"
                / source_workspace.name
            )
            prepared.isolated_workspace = str(
                replay_root / prepared.replay_id
            )
        return prepared

    def _catalog_imported_trace(
        self,
        imported: ImportedTraceArchive,
    ) -> None:
        trace = imported.trace
        try:
            self.state_store.get_run(trace.run_id)
        except RunNotFoundError:
            status = {
                TraceStatus.SUCCEEDED: RunStatus.SUCCEEDED,
                TraceStatus.CANCELLED: RunStatus.CANCELLED,
            }.get(trace.status, RunStatus.FAILED)
            run = RunRecord(
                run_id=trace.run_id,
                original_task="Imported deterministic trace; task text omitted.",
                workflow_type="imported-trace",
                status=status,
                model="captured",
                workspace="[IMPORTED_TRACE_WORKSPACE]",
                created_at=trace.created_at,
                updated_at=trace.completed_at or trace.created_at,
                completed_at=trace.completed_at,
                graph_data={"version": 1, "tasks": []},
                metadata={
                    "imported_trace": True,
                    "trace_id": trace.trace_id,
                    "archive_root": imported.manifest.archive_root,
                    "provenance_root": imported.provenance.integrity_root,
                    "source_content_included": (
                        imported.manifest.source_content_included
                    ),
                },
            )
            self.state_store.import_trace_records(
                run,
                trace,
                imported.provenance,
            )
            return

        existing = self.state_store.find_run_trace(trace.run_id)
        if existing is None:
            raise StateStoreError(
                "Imported trace run ID collides with a run that has no trace."
            )
        if existing != trace:
            raise StateStoreError(
                "Imported trace identity collides with different local data."
            )
        provenance = self.state_store.find_run_provenance_manifest(
            trace.run_id
        )
        if provenance is None:
            self.state_store.record_provenance_manifest(imported.provenance)
        elif provenance != imported.provenance:
            raise StateStoreError(
                "Imported provenance collides with different local data."
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


class _CachedToolReplayPlanner:
    def __init__(self, planner: ToolReplayPlanner) -> None:
        self._planner = planner
        self._assessments: dict[
            tuple[str, int, str, str, str, str],
            ToolReplayAssessment,
        ] = {}

    def assess(
        self,
        envelope: CapturedToolEnvelope,
        current_descriptor: ToolDescriptor,
        *,
        mode: ReplayMode,
        isolated_workspace: str | Path = "[ISOLATED_REPLAY_WORKSPACE]",
    ) -> ToolReplayAssessment:
        descriptor_sha256 = hashlib.sha256(
            canonical_json_bytes(
                current_descriptor.model_dump(mode="json")
            )
        ).hexdigest()
        envelope_sha256 = hashlib.sha256(
            canonical_json_bytes(envelope.model_dump(mode="json"))
        ).hexdigest()
        key = (
            envelope.invocation.invocation_id,
            envelope.invocation.invocation_revision,
            mode.value,
            str(isolated_workspace),
            descriptor_sha256,
            envelope_sha256,
        )
        assessment = self._assessments.get(key)
        if assessment is None:
            assessment = self._planner.assess(
                envelope,
                current_descriptor,
                mode=mode,
                isolated_workspace=isolated_workspace,
            )
            self._assessments[key] = assessment
        return assessment


class _ReplayToolPolicyContext:
    def __init__(
        self,
        trace: Trace,
        object_store: ContentAddressedStore,
        planner: _CachedToolReplayPlanner,
        descriptors: Mapping[str, ToolDescriptor],
        *,
        request: ReplayRequest,
    ) -> None:
        self.object_store = object_store
        self.planner = planner
        self.descriptors = descriptors
        self.request = request
        self._by_parent_span: dict[str, tuple[TraceOutput, ...]] = {}
        self._by_invocation: dict[str, list[TraceOutput]] = {}
        for span in trace.spans:
            if span.span_type != TraceSpanType.TOOL_INVOCATION:
                continue
            references = tuple(
                reference
                for reference in span.output_references
                if reference.media_type == TOOL_ENVELOPE_MEDIA_TYPE
            )
            if not references:
                continue
            self._by_parent_span[span.span_id] = references
            if span.invocation_id is not None:
                self._by_invocation.setdefault(
                    span.invocation_id,
                    [],
                ).extend(references)

    def evaluate(
        self,
        span: TraceSpan,
        _loaded_inputs: list[Any],
    ) -> dict[str, Any]:
        reference = self._reference_for(span)
        envelope = load_tool_envelope(
            self.object_store,
            reference.sha256,
        )
        if (
            span.invocation_id is None
            or envelope.invocation.invocation_id != span.invocation_id
        ):
            raise ReplayIncompatibleError(
                "Captured tool policy does not match its invocation."
            )
        revision = span.attributes.get("invocation_revision")
        if (
            revision is not None
            and envelope.invocation.invocation_revision != revision
        ):
            raise ReplayIncompatibleError(
                "Captured tool policy revision does not match its invocation."
            )
        descriptor = self.descriptors.get(envelope.descriptor.name)
        if descriptor is None:
            raise ReplayIncompatibleError(
                "Current tool descriptor is unavailable for policy replay."
            )
        assessment = self.planner.assess(
            envelope,
            descriptor,
            mode=self.request.mode,
            isolated_workspace=(
                self.request.isolated_workspace
                or "[ISOLATED_REPLAY_WORKSPACE]"
            ),
        )
        if assessment.current_decision is None:
            raise ReplayIncompatibleError(
                "Current tool policy could not validate the captured invocation."
            )
        return safe_protocol_dict(assessment.current_decision)

    def _reference_for(self, span: TraceSpan) -> TraceOutput:
        references = (
            self._by_parent_span.get(span.parent_span_id or "")
            or tuple(
                self._by_invocation.get(span.invocation_id or "", ())
            )
        )
        unique = {
            reference.sha256: reference for reference in references
        }
        if len(unique) != 1:
            raise ReplayIncompatibleError(
                "Policy replay requires one matching captured tool envelope."
            )
        return next(iter(unique.values()))


def _validated_tool_descriptors(
    descriptors: Mapping[str, ToolDescriptor],
) -> dict[str, ToolDescriptor]:
    validated: dict[str, ToolDescriptor] = {}
    for name, descriptor in descriptors.items():
        if descriptor.name != name:
            raise ValueError(
                "Replay tool descriptor mapping key must match its name."
            )
        validated[name] = descriptor.model_copy(deep=True)
    return validated


__all__ = [
    "ArchiveReplayResult",
    "TraceReplayService",
    "TraceVerificationReport",
]
