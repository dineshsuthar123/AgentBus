from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from agentbus.intelligence.discovery import (
    DiscoveredFile,
    DiscoveryLimits,
    ProjectDiscovery,
    RepositoryInventory,
    RepositoryInventoryScanner,
)
from agentbus.intelligence.errors import (
    IndexUnavailableError,
    RepositoryIntelligenceError,
)
from agentbus.intelligence.fingerprints import (
    content_hash,
    file_set_fingerprint,
    graph_fingerprint,
    parser_versions_fingerprint,
    project_map_fingerprint,
)
from agentbus.intelligence.graph import DependencyGraphBuilder
from agentbus.intelligence.identities import (
    file_id,
    module_id,
    reference_id,
    snapshot_id,
    stable_hash,
    symbol_id,
)
from agentbus.intelligence.invalidation import (
    DependencyInvalidator,
    InvalidationLimits,
    InvalidationPlan,
)
from agentbus.intelligence.languages import source_language_for_path
from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    IndexOperation,
    IndexOperationKind,
    IndexOperationState,
    IndexSnapshot,
    IndexState,
    Module,
    Project,
    ProjectKind,
    RepositoryIdentity,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolReference,
    WorkspaceIdentity,
    _relative_path,
)
from agentbus.intelligence.operations import IndexOperationLease
from agentbus.intelligence.parsers import (
    CancellationSignal,
    ParseRequest,
    ParseResult,
    ParserLimits,
    ParserRegistry,
    default_parser_registry,
)
from agentbus.intelligence.scheduler import (
    BoundedIndexScheduler,
    IndexProgressEvent,
    IndexProgressPhase,
    IndexProgressReporter,
    IndexProgressSink,
    IndexSchedulerLimits,
)
from agentbus.intelligence.storage import IndexStore


_PROJECT_KIND_BY_LANGUAGE = {
    SourceLanguage.GO: ProjectKind.GO,
    SourceLanguage.JAVA: ProjectKind.JAVA,
    SourceLanguage.JAVASCRIPT: ProjectKind.NODE,
    SourceLanguage.PYTHON: ProjectKind.PYTHON,
    SourceLanguage.TYPESCRIPT: ProjectKind.NODE,
}
_MAX_SNAPSHOT_DIAGNOSTICS = 1_000
_MAX_PENDING_INVALIDATION_PATHS = 256
_MAX_PENDING_INVALIDATION_CHARS = 4_000


@dataclass(frozen=True)
class IndexingResult:
    snapshot: IndexSnapshot
    indexed_paths: tuple[str, ...]
    reused_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    renamed_paths: tuple[tuple[str, str], ...] = ()
    invalidated_paths: tuple[str, ...] = ()
    invalidation_plan: InvalidationPlan | None = None
    operation: IndexOperation | None = None
    progress_events: tuple[IndexProgressEvent, ...] = ()
    maximum_active_workers: int = 0
    unchanged: bool = False


@dataclass(frozen=True)
class _ParsedUnit:
    source: SourceFile
    result: ParseResult
    project: Project | None


@dataclass(frozen=True)
class _SymbolDraft:
    unit: _ParsedUnit
    definition_index: int
    identity: str
    module_identity: str | None


@dataclass(frozen=True)
class _FileIndexOutcome:
    observed_hash: str | None = None
    unit: _ParsedUnit | None = None
    carried_source: SourceFile | None = None
    diagnostics: tuple[IndexDiagnostic, ...] = ()
    reused: bool = False
    skipped: bool = False
    partial: bool = False
    cancelled: bool = False


class RepositoryIndexer:
    """Build portable, content-addressed repository intelligence snapshots."""

    def __init__(
        self,
        workspace: str | Path,
        repository: RepositoryIdentity,
        workspace_identity: WorkspaceIdentity,
        store: IndexStore,
        *,
        registry: ParserRegistry | None = None,
        discovery_limits: DiscoveryLimits | None = None,
        parser_limits: ParserLimits | None = None,
        invalidation_limits: InvalidationLimits | None = None,
        operation_stale_after: timedelta = timedelta(minutes=5),
        operation_heartbeat_seconds: float = 5.0,
        operation_owner_pid: int | None = None,
        operation_clock: Callable[[], datetime] | None = None,
        operation_monotonic: Callable[[], float] | None = None,
        maximum_progress_events: int = 1_000,
        scheduler_limits: IndexSchedulerLimits | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise IndexUnavailableError(
                "Repository intelligence workspace is not a directory."
            )
        self.repository = RepositoryIdentity.model_validate(
            repository.model_dump(mode="python")
        )
        self.workspace_identity = WorkspaceIdentity.model_validate(
            workspace_identity.model_dump(mode="python")
        )
        if self.workspace_identity.repository_id != self.repository.repository_id:
            raise ValueError(
                "workspace and repository identities must refer to the same repository"
            )
        self.store = store
        self.registry = registry or default_parser_registry()
        self.discovery_limits = discovery_limits or DiscoveryLimits()
        self.parser_limits = parser_limits or ParserLimits(
            maximum_source_bytes=self.discovery_limits.maximum_file_bytes
        )
        self.invalidator = DependencyInvalidator(
            limits=invalidation_limits
        )
        self.operation_stale_after = operation_stale_after
        self.operation_heartbeat_seconds = operation_heartbeat_seconds
        self.operation_owner_pid = operation_owner_pid
        self.operation_clock = operation_clock
        self.operation_monotonic = operation_monotonic
        if maximum_progress_events < 2 or maximum_progress_events > 100_000:
            raise ValueError(
                "maximum_progress_events must be between 2 and 100000"
            )
        self.maximum_progress_events = maximum_progress_events
        self.scheduler = BoundedIndexScheduler(
            limits=(
                scheduler_limits
                or IndexSchedulerLimits(
                    maximum_workers=1,
                    maximum_in_flight=1,
                )
            )
        )

    def build(
        self,
        *,
        cancellation: CancellationSignal | None = None,
        operation_id: str | None = None,
        progress_sink: IndexProgressSink | None = None,
    ) -> IndexingResult:
        return self._run_operation(
            IndexOperationKind.BUILD,
            cancellation=cancellation,
            operation_id=operation_id,
            progress_sink=progress_sink,
        )

    def _run_operation(
        self,
        operation_kind: IndexOperationKind,
        *,
        cancellation: CancellationSignal | None,
        operation_id: str | None,
        progress_sink: IndexProgressSink | None,
    ) -> IndexingResult:
        lease = IndexOperationLease(
            self.store,
            self.repository,
            operation_kind,
            operation_id=operation_id,
            owner_pid=self.operation_owner_pid,
            stale_after=self.operation_stale_after,
            heartbeat_interval_seconds=self.operation_heartbeat_seconds,
            cancellation=cancellation,
            clock=self.operation_clock,
            monotonic=self.operation_monotonic,
        )
        operation = lease.acquire()
        reporter = IndexProgressReporter(
            operation.operation_id,
            progress_sink,
            maximum_events=self.maximum_progress_events,
        )
        reporter.emit(
            IndexProgressPhase.DISCOVERY,
            completed_items=0,
            total_items=0,
            message="Repository discovery started.",
        )
        try:
            result = self._build_snapshot(
                cancellation=lease,
                lease=lease,
                reporter=reporter,
            )
            paused = result.snapshot.state == IndexState.PAUSED
            finished = lease.finish(
                IndexOperationState.PAUSED
                if paused
                else IndexOperationState.COMPLETED
            )
            reporter.emit(
                (
                    IndexProgressPhase.PAUSED
                    if paused
                    else IndexProgressPhase.COMPLETED
                ),
                completed_items=result.snapshot.file_count,
                total_items=result.snapshot.file_count,
                message=(
                    "Repository indexing paused."
                    if paused
                    else "Repository indexing completed."
                ),
                terminal=True,
            )
            return replace(
                result,
                operation=finished,
                progress_events=reporter.events,
            )
        except BaseException:
            lease.fail()
            events = reporter.events
            last = events[-1] if events else None
            try:
                reporter.emit(
                    IndexProgressPhase.FAILED,
                    completed_items=(
                        last.completed_items if last is not None else 0
                    ),
                    total_items=last.total_items if last is not None else 0,
                    message="Repository indexing failed.",
                    terminal=True,
                )
            except Exception:
                pass
            raise

    def _build_snapshot(
        self,
        *,
        cancellation: CancellationSignal,
        lease: IndexOperationLease,
        reporter: IndexProgressReporter,
    ) -> IndexingResult:
        started_at = lease.operation.started_at
        inventory = RepositoryInventoryScanner(
            self.workspace,
            limits=self.discovery_limits,
        ).scan()
        discovery = ProjectDiscovery(
            self.workspace,
            self.repository,
            limits=self.discovery_limits,
        ).discover_inventory(inventory)
        projects = tuple(
            sorted(discovery.projects, key=lambda item: item.project_id)
        )
        parser_versions = self.registry.versions()
        project_hash = project_map_fingerprint(projects)
        configuration_hash, configuration_diagnostics = (
            _configuration_fingerprint(
                inventory,
                projects,
                self.discovery_limits,
            )
        )
        diagnostics = [
            IndexDiagnostic(
                code="index.configuration_fingerprint",
                severity=DiagnosticSeverity.INFO,
                message="Repository configuration fingerprint captured.",
                recoverable=True,
                details={"fingerprint": configuration_hash},
            ),
            *discovery.diagnostics,
            *configuration_diagnostics,
        ]
        previous = self.store.latest_snapshot(
            self.repository.repository_id
        )
        if (
            previous is not None
            and previous.workspace_id
            != self.workspace_identity.workspace_id
        ):
            previous = None
        previous_files = (
            self.store.list_files(previous.snapshot_id)
            if previous is not None
            else ()
        )
        previous_files_by_path = {
            source.relative_path: source
            for source in previous_files
        }
        reuse_enabled = bool(
            previous is not None
            and previous.state
            not in {IndexState.CORRUPTED, IndexState.INCOMPATIBLE}
            and previous.project_map_hash == project_hash
            and _snapshot_configuration_fingerprint(previous)
            == configuration_hash
        )
        units_by_path: dict[str, _ParsedUnit] = {}
        carried_sources: dict[str, SourceFile] = {}
        observed_hashes: dict[str, str] = {}
        indexed_paths: set[str] = set()
        reused_paths: set[str] = set()
        skipped_paths: set[str] = set()
        processed_paths: set[str] = set()
        parser_partial = False
        paused = False
        maximum_active_workers = 0
        supported_files = tuple(
            item
            for item in discovery.files
            if source_language_for_path(item.relative_path) is not None
        )
        direct_batch = self.scheduler.run(
            supported_files,
            lambda discovered: self._index_file(
                inventory,
                discovered,
                projects,
                previous_files_by_path.get(discovered.relative_path),
                reuse_enabled=reuse_enabled,
                cancellation=cancellation,
            ),
            cancellation=cancellation,
            reporter=reporter,
            phase=IndexProgressPhase.INDEXING,
            item_path=lambda discovered: discovered.relative_path,
        )
        maximum_active_workers = direct_batch.maximum_active_workers
        for scheduled in direct_batch.completed:
            discovered = scheduled.item
            outcome = scheduled.result
            processed_paths.add(discovered.relative_path)
            if outcome.observed_hash is not None:
                observed_hashes[discovered.relative_path] = (
                    outcome.observed_hash
                )
            if outcome.unit is not None:
                units_by_path[discovered.relative_path] = outcome.unit
                indexed_paths.add(discovered.relative_path)
            if outcome.carried_source is not None:
                carried_sources[discovered.relative_path] = (
                    outcome.carried_source
                )
            if outcome.reused:
                reused_paths.add(discovered.relative_path)
            if outcome.skipped:
                skipped_paths.add(discovered.relative_path)
            diagnostics.extend(outcome.diagnostics)
            parser_partial = parser_partial or outcome.partial
            if outcome.cancelled:
                paused = True
        if direct_batch.cancelled or _cancelled(cancellation):
            paused = True

        if paused and reuse_enabled:
            discovered_paths = {
                item.relative_path for item in supported_files
            }
            for path, source in previous_files_by_path.items():
                if path in discovered_paths and path not in processed_paths:
                    carried_sources[path] = source

        _, preliminary_symbols, _ = _materialize_records(
            list(units_by_path.values())
        )
        previous_modules = (
            self.store.list_modules(previous.snapshot_id)
            if previous is not None and reuse_enabled
            else ()
        )
        previous_symbols = (
            self.store.list_symbols(previous.snapshot_id)
            if previous is not None and reuse_enabled
            else ()
        )
        previous_references = (
            self.store.list_references(previous.snapshot_id)
            if previous is not None and reuse_enabled
            else ()
        )
        preliminary_files = tuple(
            sorted(
                (
                    *carried_sources.values(),
                    *(unit.source for unit in units_by_path.values()),
                ),
                key=lambda item: item.file_id,
            )
        )
        current_paths = {
            item.relative_path for item in supported_files
        }
        removed_paths = sorted(
            set(previous_files_by_path).difference(current_paths)
        )
        renamed_paths = _detect_renames(
            removed_paths,
            indexed_paths,
            previous_files_by_path,
            {
                source.relative_path: source
                for source in preliminary_files
            },
        )
        renamed_sources = {source for source, _ in renamed_paths}
        deleted_paths = [
            path for path in removed_paths if path not in renamed_sources
        ]

        invalidation_plan: InvalidationPlan | None = None
        invalidation_targets: set[str] = set()
        pending_paths, resume_requires_full = (
            _pending_invalidations(previous)
            if previous is not None and reuse_enabled
            else ((), False)
        )
        if resume_requires_full:
            invalidation_targets.update(carried_sources)
        else:
            invalidation_targets.update(
                path
                for path in pending_paths
                if path in carried_sources
            )
        if (
            previous is not None
            and reuse_enabled
            and (indexed_paths or removed_paths)
        ):
            invalidation_plan = self.invalidator.plan(
                previous_files,
                previous_symbols,
                previous_references,
                changed_paths=indexed_paths,
                deleted_paths=deleted_paths,
                renamed_paths=renamed_paths,
                changed_qualified_names=(
                    symbol.qualified_name
                    for symbol in preliminary_symbols
                ),
            )
            if invalidation_plan.requires_full_reindex:
                invalidation_targets.update(carried_sources)
                _append_diagnostic(
                    diagnostics,
                    IndexDiagnostic(
                        code="index.invalidation_fallback",
                        severity=DiagnosticSeverity.WARNING,
                        message=(
                            "Dependency invalidation reached a safety bound; "
                            "remaining files require a full local reindex."
                        ),
                        recoverable=True,
                        details={
                            "dependent_count": len(
                                invalidation_plan.dependent_paths
                            ),
                            "inspected_references": (
                                invalidation_plan.inspected_references
                            ),
                        },
                    ),
                )
            else:
                invalidation_targets.update(
                    path
                    for path in invalidation_plan.dependent_paths
                    if path in carried_sources
                )
            _append_diagnostic(
                diagnostics,
                IndexDiagnostic(
                    code="index.dependency_invalidation",
                    severity=DiagnosticSeverity.INFO,
                    message=(
                        "Reverse dependencies were evaluated for changed "
                        "repository records."
                    ),
                    recoverable=True,
                    details={
                        "direct_count": len(
                            invalidation_plan.direct_paths
                        ),
                        "dependent_count": len(
                            invalidation_plan.dependent_paths
                        ),
                        "full_reindex": (
                            invalidation_plan.requires_full_reindex
                        ),
                    },
                ),
            )

        invalidated_paths: set[str] = set()
        ordered_targets = tuple(
            sorted(
                path
                for path in invalidation_targets
                if path not in skipped_paths
            )
        )
        pending_targets: tuple[str, ...] = ()
        retry_targets: set[str] = set()
        supported_by_path = {
            item.relative_path: item for item in supported_files
        }
        if paused:
            pending_targets = ordered_targets
        elif ordered_targets:
            invalidation_batch = self.scheduler.run(
                ordered_targets,
                lambda path: self._index_file(
                    inventory,
                    supported_by_path[path],
                    projects,
                    previous_files_by_path.get(path),
                    reuse_enabled=reuse_enabled,
                    force=True,
                    cancellation=cancellation,
                ),
                cancellation=cancellation,
                reporter=reporter,
                phase=IndexProgressPhase.INVALIDATION,
                item_path=lambda path: path,
            )
            maximum_active_workers = max(
                maximum_active_workers,
                invalidation_batch.maximum_active_workers,
            )
            pending_targets = invalidation_batch.pending
            if invalidation_batch.cancelled:
                paused = True
            for scheduled in invalidation_batch.completed:
                path = scheduled.item
                outcome = scheduled.result
                if outcome.observed_hash is not None:
                    observed_hashes[path] = outcome.observed_hash
                reused_paths.discard(path)
                if outcome.unit is not None:
                    units_by_path[path] = outcome.unit
                    carried_sources.pop(path, None)
                    indexed_paths.add(path)
                    invalidated_paths.add(path)
                elif outcome.carried_source is not None:
                    carried_sources[path] = outcome.carried_source
                if outcome.skipped:
                    skipped_paths.add(path)
                    retry_targets.add(path)
                diagnostics.extend(outcome.diagnostics)
                parser_partial = parser_partial or outcome.partial
                if outcome.cancelled:
                    paused = True
                    retry_targets.add(path)
        remaining_targets = tuple(
            sorted({*pending_targets, *retry_targets})
        )
        if remaining_targets:
            _append_diagnostic(
                diagnostics,
                _pending_invalidation_diagnostic(remaining_targets),
            )

        new_modules, new_symbols, new_references = _materialize_records(
            list(units_by_path.values())
        )
        reused_file_ids = {
            source.file_id for source in carried_sources.values()
        }
        reused_modules = tuple(
            item
            for item in previous_modules
            if item.relative_path in carried_sources
        )
        reused_symbols = tuple(
            item
            for item in previous_symbols
            if item.file_id in reused_file_ids
        )
        reused_references = tuple(
            item
            for item in previous_references
            if item.source_file_id in reused_file_ids
        )
        modules = _unique_modules((*reused_modules, *new_modules))
        symbols = _unique_symbols((*reused_symbols, *new_symbols))
        references = _rebind_references(
            (*reused_references, *new_references),
            symbols,
            modules,
            previous_symbols,
        )
        files = tuple(
            sorted(
                (
                    *carried_sources.values(),
                    *(unit.source for unit in units_by_path.values()),
                ),
                key=lambda item: item.file_id,
            )
        )
        source_fingerprint = stable_hash(
            {
                "inventory": discovery.inventory_fingerprint,
                "configuration": configuration_hash,
                "observed_sources": file_set_fingerprint(observed_hashes),
                "observation_complete": (
                    len(observed_hashes) == len(supported_files)
                ),
            }
        )
        project_hash = project_map_fingerprint(projects)
        graph = DependencyGraphBuilder().build(
            files,
            symbols,
            references,
        )
        edges = graph.edges
        graph_hash = graph_fingerprint(edges)
        state = _snapshot_state(
            paused=paused,
            partial=(
                parser_partial
                or discovery.truncated
                or bool(skipped_paths)
            ),
        )
        bounded_diagnostics = tuple(
            diagnostics[:_MAX_SNAPSHOT_DIAGNOSTICS]
        )
        identity_source = (
            source_fingerprint
            if state == IndexState.CURRENT
            else stable_hash(
                {
                    "source_fingerprint": source_fingerprint,
                    "state": state.value,
                    "diagnostics": [
                        {
                            "code": item.code,
                            "path": item.relative_path,
                            "details_hash": stable_hash(item.details),
                        }
                        for item in bounded_diagnostics
                    ],
                }
            )
        )
        identity = snapshot_id(
            self.repository.repository_id,
            identity_source,
            parser_versions_fingerprint(parser_versions),
            project_hash,
            graph_hash,
        )
        completed_at = (
            None if state == IndexState.PAUSED else datetime.now(timezone.utc)
        )
        snapshot = IndexSnapshot(
            snapshot_id=identity,
            repository_id=self.repository.repository_id,
            workspace_id=self.workspace_identity.workspace_id,
            state=state,
            created_at=started_at,
            completed_at=completed_at,
            file_count=len(files),
            symbol_count=len(symbols),
            reference_count=len(references),
            edge_count=len(edges),
            project_map_hash=project_hash,
            graph_hash=graph_hash,
            parser_versions=parser_versions,
            source_fingerprint=source_fingerprint,
            diagnostics=bounded_diagnostics,
        )

        reporter.emit(
            IndexProgressPhase.PERSISTENCE,
            completed_items=len(files),
            total_items=len(files),
            message="Repository index snapshot is ready to persist.",
        )
        publish_guard = lease.publish_guard()
        try:
            existing = self.store.get_snapshot(identity)
        except IndexUnavailableError:
            stored = self.store.publish_snapshot(
                self.repository,
                self.workspace_identity,
                snapshot,
                projects=projects,
                files=files,
                modules=modules,
                symbols=symbols,
                references=references,
                edges=edges,
                **publish_guard,
            )
            return IndexingResult(
                snapshot=stored,
                indexed_paths=tuple(sorted(indexed_paths)),
                reused_paths=tuple(sorted(reused_paths)),
                skipped_paths=tuple(sorted(skipped_paths)),
                deleted_paths=tuple(deleted_paths),
                renamed_paths=renamed_paths,
                invalidated_paths=tuple(sorted(invalidated_paths)),
                invalidation_plan=invalidation_plan,
                maximum_active_workers=maximum_active_workers,
            )
        return IndexingResult(
            snapshot=existing,
            indexed_paths=tuple(sorted(indexed_paths)),
            reused_paths=tuple(sorted(reused_paths)),
            skipped_paths=tuple(sorted(skipped_paths)),
            deleted_paths=tuple(deleted_paths),
            renamed_paths=renamed_paths,
            invalidated_paths=tuple(sorted(invalidated_paths)),
            invalidation_plan=invalidation_plan,
            maximum_active_workers=maximum_active_workers,
            unchanged=not any(
                (
                    indexed_paths,
                    skipped_paths,
                    deleted_paths,
                    renamed_paths,
                    invalidated_paths,
                    paused,
                )
            ),
        )

    def _index_file(
        self,
        inventory: RepositoryInventory,
        discovered: DiscoveredFile,
        projects: tuple[Project, ...],
        previous_source: SourceFile | None,
        *,
        reuse_enabled: bool,
        force: bool = False,
        cancellation: CancellationSignal | None = None,
    ) -> _FileIndexOutcome:
        language = source_language_for_path(discovered.relative_path)
        if language is None:
            raise ValueError("unsupported source file reached the indexer")
        project = _project_for_path(
            discovered.relative_path,
            language,
            projects,
        )
        observed_hash: str | None = None
        try:
            payload = inventory.read_bytes(
                discovered.relative_path,
                maximum_bytes=min(
                    self.discovery_limits.maximum_file_bytes,
                    self.parser_limits.maximum_source_bytes,
                ),
            )
            observed_hash = content_hash(payload)
            content = payload.decode("utf-8-sig")
            parser = self.registry.resolve(language)
            descriptor = parser.descriptor
            source_identity = file_id(
                self.repository.repository_id,
                discovered.relative_path,
            )
            decoded_hash = content_hash(content)
            project_identity = project.project_id if project else None
            if (
                reuse_enabled
                and not force
                and previous_source is not None
                and previous_source.content_hash == decoded_hash
                and previous_source.language == language
                and previous_source.project_id == project_identity
                and previous_source.parser_name == descriptor.name
                and previous_source.parser_version == descriptor.version
            ):
                return _FileIndexOutcome(
                    observed_hash=observed_hash,
                    carried_source=previous_source.model_copy(
                        update={
                            "size_bytes": len(payload),
                            "generated": discovered.generated,
                            "test": discovered.test,
                        }
                    ),
                    reused=True,
                )
            request = ParseRequest.from_content(
                repository_id=self.repository.repository_id,
                file_id=source_identity,
                project_id=project_identity,
                relative_path=discovered.relative_path,
                language=language,
                content=content,
            )
            result = self.registry.parse(
                request,
                limits=self.parser_limits,
                cancellation=cancellation,
            )
            source = SourceFile(
                file_id=source_identity,
                repository_id=self.repository.repository_id,
                project_id=project_identity,
                relative_path=discovered.relative_path,
                language=language,
                content_hash=result.source_hash,
                size_bytes=len(payload),
                parser_name=descriptor.name,
                parser_version=descriptor.version,
                generated=discovered.generated,
                test=discovered.test,
                protected=False,
            )
            return _FileIndexOutcome(
                observed_hash=observed_hash,
                unit=_ParsedUnit(
                    source=source,
                    result=result,
                    project=project,
                ),
                diagnostics=result.diagnostics,
                partial=result.partial,
                cancelled=result.cancelled,
            )
        except (
            UnicodeError,
            RepositoryIntelligenceError,
            ValueError,
        ) as exc:
            return _FileIndexOutcome(
                observed_hash=observed_hash,
                carried_source=(
                    previous_source if reuse_enabled else None
                ),
                diagnostics=(
                    IndexDiagnostic(
                        code="index.file_failed",
                        severity=DiagnosticSeverity.WARNING,
                        message="A source file could not be indexed safely.",
                        relative_path=discovered.relative_path,
                        recoverable=True,
                        details={"error_type": type(exc).__name__},
                    ),
                ),
                skipped=True,
                partial=True,
            )

    def update(
        self,
        *,
        cancellation: CancellationSignal | None = None,
        operation_id: str | None = None,
        progress_sink: IndexProgressSink | None = None,
    ) -> IndexingResult:
        return self._run_operation(
            IndexOperationKind.UPDATE,
            cancellation=cancellation,
            operation_id=operation_id,
            progress_sink=progress_sink,
        )


def _configuration_fingerprint(
    inventory: RepositoryInventory,
    projects: tuple[Project, ...],
    limits: DiscoveryLimits,
) -> tuple[str, tuple[IndexDiagnostic, ...]]:
    manifest_paths = sorted(
        {
            path
            for project in projects
            for path in project.manifest_paths
            if inventory.contains(path)
        }
    )
    hashes: dict[str, str] = {}
    diagnostics: list[IndexDiagnostic] = []
    for path in manifest_paths[:1_024]:
        try:
            payload = inventory.read_bytes(
                path,
                maximum_bytes=min(
                    limits.maximum_metadata_bytes,
                    limits.maximum_file_bytes,
                ),
            )
            hashes[path] = content_hash(payload)
        except RepositoryIntelligenceError as exc:
            _append_diagnostic(
                diagnostics,
                IndexDiagnostic(
                    code="index.configuration_unreadable",
                    severity=DiagnosticSeverity.WARNING,
                    message="A project configuration file could not be hashed.",
                    relative_path=path,
                    recoverable=True,
                    details={"error_type": type(exc).__name__},
                ),
            )
    return stable_hash(hashes), tuple(diagnostics)


def _snapshot_configuration_fingerprint(
    snapshot: IndexSnapshot,
) -> str | None:
    for diagnostic in snapshot.diagnostics:
        if diagnostic.code != "index.configuration_fingerprint":
            continue
        fingerprint = diagnostic.details.get("fingerprint")
        if isinstance(fingerprint, str):
            return fingerprint
    return None


def _pending_invalidation_diagnostic(
    paths: Iterable[str],
) -> IndexDiagnostic:
    normalized = tuple(sorted({_relative_path(path) for path in paths}))
    bounded: list[str] = []
    character_count = 0
    for path in normalized:
        if len(bounded) >= _MAX_PENDING_INVALIDATION_PATHS:
            break
        if character_count + len(path) > _MAX_PENDING_INVALIDATION_CHARS:
            break
        bounded.append(path)
        character_count += len(path)
    truncated = len(bounded) != len(normalized)
    return IndexDiagnostic(
        code="index.invalidation_pending",
        severity=DiagnosticSeverity.INFO,
        message=(
            "Dependency-aware indexing has bounded work remaining."
        ),
        recoverable=True,
        details={
            "paths": bounded,
            "pending_count": len(normalized),
            "requires_full_reindex": truncated,
        },
    )


def _pending_invalidations(
    snapshot: IndexSnapshot,
) -> tuple[tuple[str, ...], bool]:
    pending: set[str] = set()
    requires_full_reindex = False
    for diagnostic in snapshot.diagnostics:
        if diagnostic.code != "index.invalidation_pending":
            continue
        raw_paths = diagnostic.details.get("paths", ())
        if not isinstance(raw_paths, (list, tuple)):
            requires_full_reindex = True
            continue
        for path in raw_paths:
            if not isinstance(path, str):
                requires_full_reindex = True
                continue
            try:
                pending.add(_relative_path(path))
            except ValueError:
                requires_full_reindex = True
        requires_full_reindex = requires_full_reindex or bool(
            diagnostic.details.get("requires_full_reindex")
        )
    return tuple(sorted(pending)), requires_full_reindex


def _unique_modules(modules: tuple[Module, ...]) -> tuple[Module, ...]:
    by_identity: dict[str, Module] = {}
    for module in modules:
        existing = by_identity.get(module.module_id)
        if existing is not None and existing != module:
            raise ValueError("incremental index produced conflicting modules")
        by_identity[module.module_id] = module
    return tuple(
        sorted(by_identity.values(), key=lambda item: item.module_id)
    )


def _unique_symbols(symbols: tuple[Symbol, ...]) -> tuple[Symbol, ...]:
    by_identity: dict[str, Symbol] = {}
    for symbol in symbols:
        existing = by_identity.get(symbol.symbol_id)
        if existing is not None and existing != symbol:
            raise ValueError("incremental index produced conflicting symbols")
        by_identity[symbol.symbol_id] = symbol
    return tuple(
        sorted(by_identity.values(), key=lambda item: item.symbol_id)
    )


def _rebind_references(
    references: tuple[SymbolReference, ...],
    symbols: tuple[Symbol, ...],
    modules: tuple[Module, ...],
    previous_symbols: tuple[Symbol, ...],
) -> tuple[SymbolReference, ...]:
    symbols_by_id = {item.symbol_id: item for item in symbols}
    previous_by_id = {
        item.symbol_id: item for item in previous_symbols
    }
    symbols_by_qualified_name: dict[str, list[str]] = defaultdict(list)
    for symbol in symbols:
        symbols_by_qualified_name[symbol.qualified_name].append(
            symbol.symbol_id
        )
    modules_by_id = {item.module_id: item for item in modules}
    rebound: dict[str, SymbolReference] = {}
    for reference in references:
        source = (
            symbols_by_id.get(reference.source_symbol_id)
            if reference.source_symbol_id
            else None
        )
        current_target = (
            symbols_by_id.get(reference.target_symbol_id)
            if reference.target_symbol_id
            else None
        )
        previous_target = (
            previous_by_id.get(reference.target_symbol_id)
            if reference.target_symbol_id
            else None
        )
        target = (
            reference.unresolved_target
            or (
                current_target.qualified_name
                if current_target is not None
                else None
            )
            or (
                previous_target.qualified_name
                if previous_target is not None
                else None
            )
        )
        if target is None:
            continue
        module = (
            modules_by_id.get(source.module_id)
            if source is not None and source.module_id
            else None
        )
        target_identity = _resolve_target(
            target,
            source.qualified_name if source else None,
            module,
            symbols_by_qualified_name,
        )
        rebound_reference = SymbolReference(
            reference_id=reference.reference_id,
            source_symbol_id=(
                source.symbol_id if source is not None else None
            ),
            source_file_id=reference.source_file_id,
            target_symbol_id=target_identity,
            unresolved_target=None if target_identity else target,
            kind=reference.kind,
            location=reference.location,
            confidence=reference.confidence,
            explanation=reference.explanation,
        )
        existing = rebound.get(reference.reference_id)
        if existing is not None and existing != rebound_reference:
            raise ValueError(
                "incremental index produced conflicting references"
            )
        rebound[reference.reference_id] = rebound_reference
    return tuple(
        sorted(rebound.values(), key=lambda item: item.reference_id)
    )


def _detect_renames(
    deleted_paths: Iterable[str],
    indexed_paths: Iterable[str],
    previous_files: dict[str, SourceFile],
    current_files: dict[str, SourceFile],
) -> tuple[tuple[str, str], ...]:
    added_paths = sorted(
        path for path in indexed_paths if path not in previous_files
    )
    available = set(deleted_paths)
    renames: list[tuple[str, str]] = []
    for target in added_paths:
        current = current_files.get(target)
        if current is None:
            continue
        source = next(
            (
                path
                for path in sorted(available)
                if previous_files[path].content_hash == current.content_hash
                and previous_files[path].language == current.language
            ),
            None,
        )
        if source is None:
            continue
        available.remove(source)
        renames.append((source, target))
    return tuple(renames)


def _materialize_records(
    units: list[_ParsedUnit],
) -> tuple[
    tuple[Module, ...],
    tuple[Symbol, ...],
    tuple[SymbolReference, ...],
]:
    modules: dict[str, Module] = {}
    drafts: list[_SymbolDraft] = []
    symbol_ids_by_file_name: dict[
        tuple[str, str],
        list[str],
    ] = defaultdict(list)
    symbols_by_qualified_name: dict[str, list[str]] = defaultdict(list)

    for unit in units:
        module = _module_for_unit(unit)
        if module is not None:
            modules[module.module_id] = module
        ordinals: dict[tuple[str, SymbolKind, str | None], int] = defaultdict(int)
        for index, definition in enumerate(unit.result.definitions):
            key = (
                definition.qualified_name,
                definition.kind,
                definition.signature,
            )
            ordinal = ordinals[key]
            ordinals[key] += 1
            identity = symbol_id(
                unit.source.file_id,
                definition.qualified_name,
                definition.kind,
                signature=definition.signature,
                ordinal=ordinal,
            )
            drafts.append(
                _SymbolDraft(
                    unit=unit,
                    definition_index=index,
                    identity=identity,
                    module_identity=module.module_id if module else None,
                )
            )
            symbol_ids_by_file_name[
                (unit.source.file_id, definition.qualified_name)
            ].append(identity)
            symbols_by_qualified_name[definition.qualified_name].append(identity)

    symbols: list[Symbol] = []
    for draft in drafts:
        definition = draft.unit.result.definitions[draft.definition_index]
        parent_candidates = symbol_ids_by_file_name.get(
            (
                draft.unit.source.file_id,
                definition.parent_qualified_name or "",
            ),
            (),
        )
        symbols.append(
            Symbol(
                symbol_id=draft.identity,
                file_id=draft.unit.source.file_id,
                project_id=draft.unit.source.project_id,
                module_id=draft.module_identity,
                name=definition.name,
                qualified_name=definition.qualified_name,
                kind=definition.kind,
                language=draft.unit.source.language,
                location=definition.location,
                signature=definition.signature,
                documentation=definition.documentation,
                parent_symbol_id=(
                    parent_candidates[0] if parent_candidates else None
                ),
                exported=definition.exported,
                test=definition.test,
                endpoint=definition.endpoint,
                confidence=definition.confidence,
                attributes=definition.attributes,
            )
        )

    references: dict[str, SymbolReference] = {}
    for unit in units:
        module = _module_for_unit(unit)
        for parsed in unit.result.references:
            source_candidates = symbol_ids_by_file_name.get(
                (
                    unit.source.file_id,
                    parsed.source_qualified_name or "",
                ),
                (),
            )
            target_identity = _resolve_target(
                parsed.target,
                parsed.source_qualified_name,
                module,
                symbols_by_qualified_name,
            )
            identity = reference_id(
                unit.source.file_id,
                parsed.location.relative_path,
                parsed.location.start_line,
                parsed.location.start_column,
                parsed.target,
                parsed.kind.value,
            )
            references.setdefault(
                identity,
                SymbolReference(
                    reference_id=identity,
                    source_symbol_id=(
                        source_candidates[0] if source_candidates else None
                    ),
                    source_file_id=unit.source.file_id,
                    target_symbol_id=target_identity,
                    unresolved_target=(
                        None if target_identity else parsed.target
                    ),
                    kind=parsed.kind,
                    location=parsed.location,
                    confidence=parsed.confidence,
                    explanation=parsed.explanation,
                ),
            )

    return (
        tuple(sorted(modules.values(), key=lambda item: item.module_id)),
        tuple(sorted(symbols, key=lambda item: item.symbol_id)),
        tuple(sorted(references.values(), key=lambda item: item.reference_id)),
    )


def _module_for_unit(unit: _ParsedUnit) -> Module | None:
    if unit.project is None:
        return None
    definition = next(
        (
            item
            for item in unit.result.definitions
            if item.kind in {SymbolKind.MODULE, SymbolKind.PACKAGE}
        ),
        None,
    )
    qualified_name = (
        definition.qualified_name
        if definition
        else PurePosixPath(unit.source.relative_path).with_suffix("").as_posix()
    )
    name = (
        definition.name
        if definition
        else PurePosixPath(unit.source.relative_path).stem
    )
    identity = module_id(
        unit.project.project_id,
        unit.source.relative_path,
        qualified_name,
    )
    return Module(
        module_id=identity,
        project_id=unit.project.project_id,
        name=name,
        qualified_name=qualified_name,
        relative_path=unit.source.relative_path,
        language=unit.source.language,
        public=bool(definition and definition.exported),
    )


def _resolve_target(
    target: str,
    source_qualified_name: str | None,
    module: Module | None,
    symbols_by_qualified_name: dict[str, list[str]],
) -> str | None:
    candidates = [target]
    if module is not None and not target.startswith("."):
        candidates.append(f"{module.qualified_name}.{target}")
    if source_qualified_name and not target.startswith("."):
        parts = source_qualified_name.split(".")
        for end in range(len(parts) - 1, 0, -1):
            candidates.append(f"{'.'.join(parts[:end])}.{target}")
    for candidate in dict.fromkeys(candidates):
        identities = symbols_by_qualified_name.get(candidate, ())
        if len(identities) == 1:
            return identities[0]
    return None


def _project_for_path(
    relative_path: str,
    language: SourceLanguage,
    projects: tuple[Project, ...],
) -> Project | None:
    expected = _PROJECT_KIND_BY_LANGUAGE[language]
    candidates = [
        project
        for project in projects
        if project.kind in {expected, ProjectKind.GENERIC}
        and _contains_path(project.root, relative_path)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            -len(PurePosixPath(item.root).parts) if item.root else 0,
            item.kind != expected,
            item.project_id,
        ),
    )


def _contains_path(root: str, relative_path: str) -> bool:
    if not root:
        return True
    return relative_path == root or relative_path.startswith(f"{root}/")


def _snapshot_state(*, paused: bool, partial: bool) -> IndexState:
    if paused:
        return IndexState.PAUSED
    if partial:
        return IndexState.PARTIALLY_CURRENT
    return IndexState.CURRENT


def _append_diagnostic(
    diagnostics: list[IndexDiagnostic],
    diagnostic: IndexDiagnostic,
) -> None:
    if len(diagnostics) < _MAX_SNAPSHOT_DIAGNOSTICS:
        diagnostics.append(diagnostic)


def _cancelled(cancellation: CancellationSignal | None) -> bool:
    return bool(cancellation is not None and cancellation.is_set())
