from __future__ import annotations

import hashlib
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Literal

from pydantic import Field

from agentbus.intelligence.context import (
    ContextPlanner,
    ContextPlanningRequest,
)
from agentbus.intelligence.discovery import RepositoryInventoryScanner
from agentbus.intelligence.errors import (
    IndexUnavailableError,
    RepositoryQueryError,
)
from agentbus.intelligence.freshness import IndexFreshnessChecker
from agentbus.intelligence.hybrid import HybridRetriever
from agentbus.intelligence.identities import (
    repository_identity,
    workspace_identity,
)
from agentbus.intelligence.impact import ChangeImpactAnalyzer
from agentbus.intelligence.indexer import IndexingResult, RepositoryIndexer
from agentbus.intelligence.models import (
    ArchitectureBoundary,
    ContextPlan,
    ContextRole,
    DependencyEdge,
    DependencyKind,
    ImpactRequest,
    ImpactResult,
    IndexOperationKind,
    IndexSnapshot,
    IndexState,
    IndexStatus,
    IntelligenceModel,
    Module,
    OwnershipRule,
    Project,
    ProjectKind,
    SearchQuery,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    TestImpactResult,
    _relative_path,
)
from agentbus.intelligence.search import RepositoryLexicalIndex
from agentbus.intelligence.storage import IndexStore
from agentbus.intelligence.test_impact import TestImpactSelector
from agentbus.intelligence.traversal import DependencyGraph, TraversalResult


_MAX_MAINTENANCE_PATHS = 1_000
_MAX_QUERY_RESULTS = 200
_MAX_OVERVIEW_PROJECTS = 256
_MAX_OVERVIEW_MODULES = 1_000
_MAX_OVERVIEW_RULES = 500


class IndexMutationReport(IntelligenceModel):
    operation: IndexOperationKind
    snapshot: IndexSnapshot
    status: IndexStatus
    operation_id: str | None = Field(default=None, max_length=128)
    unchanged: bool = False
    indexed_count: int = Field(ge=0)
    reused_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    deleted_count: int = Field(ge=0)
    renamed_count: int = Field(ge=0)
    invalidated_count: int = Field(ge=0)
    indexed_paths: tuple[str, ...] = Field(
        default=(),
        max_length=_MAX_MAINTENANCE_PATHS,
    )
    reused_paths: tuple[str, ...] = Field(
        default=(),
        max_length=_MAX_MAINTENANCE_PATHS,
    )
    skipped_paths: tuple[str, ...] = Field(
        default=(),
        max_length=_MAX_MAINTENANCE_PATHS,
    )
    deleted_paths: tuple[str, ...] = Field(
        default=(),
        max_length=_MAX_MAINTENANCE_PATHS,
    )
    invalidated_paths: tuple[str, ...] = Field(
        default=(),
        max_length=_MAX_MAINTENANCE_PATHS,
    )
    path_reporting_truncated: bool = False
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class IndexVerificationReport(IntelligenceModel):
    valid: bool
    fresh: bool
    schema_version: int = Field(ge=1)
    status: IndexStatus
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class ProjectSummary(IntelligenceModel):
    project_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    kind: ProjectKind
    root: str = Field(max_length=2_048)
    source_roots: tuple[str, ...] = Field(default=(), max_length=128)
    test_roots: tuple[str, ...] = Field(default=(), max_length=128)
    file_count: int = Field(ge=0)
    symbol_count: int = Field(ge=0)
    languages: tuple[SourceLanguage, ...] = Field(default=(), max_length=32)


class LanguageSummary(IntelligenceModel):
    language: SourceLanguage
    file_count: int = Field(ge=0)
    symbol_count: int = Field(ge=0)


class ModuleSummary(IntelligenceModel):
    module_id: str = Field(min_length=1, max_length=256)
    project_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=512)
    qualified_name: str = Field(min_length=1, max_length=2_048)
    relative_path: str = Field(min_length=1, max_length=2_048)
    language: SourceLanguage
    public: bool = False
    symbol_count: int = Field(ge=0)


class OwnershipRuleSummary(IntelligenceModel):
    rule_id: str = Field(min_length=1, max_length=256)
    pattern: str = Field(min_length=1, max_length=2_048)
    owners: tuple[str, ...] = Field(min_length=1, max_length=128)
    source_path: str = Field(min_length=1, max_length=2_048)
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=2_048)


class ArchitectureBoundarySummary(IntelligenceModel):
    boundary_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=512)
    boundary_type: Literal[
        "layer",
        "component",
        "service",
        "shared_library",
        "generated",
        "security_sensitive",
        "forbidden_dependency",
    ]
    scope: tuple[str, ...] = Field(min_length=1, max_length=256)
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=2_048)
    forbidden_targets: tuple[str, ...] = Field(default=(), max_length=256)


class RepositoryOverview(IntelligenceModel):
    snapshot_id: str = Field(min_length=1, max_length=256)
    index_state: IndexState
    projects: tuple[ProjectSummary, ...] = Field(
        default=(),
        max_length=_MAX_OVERVIEW_PROJECTS,
    )
    languages: tuple[LanguageSummary, ...] = Field(default=(), max_length=32)
    modules: tuple[ModuleSummary, ...] = Field(
        default=(),
        max_length=_MAX_OVERVIEW_MODULES,
    )
    symbol_kind_counts: dict[str, int] = Field(default_factory=dict)
    ownership_rules: tuple[OwnershipRuleSummary, ...] = Field(
        default=(),
        max_length=_MAX_OVERVIEW_RULES,
    )
    architecture_boundaries: tuple[ArchitectureBoundarySummary, ...] = Field(
        default=(),
        max_length=_MAX_OVERVIEW_RULES,
    )
    truncated: bool = False
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class IndexGarbageCollectionReport(IntelligenceModel):
    retained_snapshots: int = Field(ge=1, le=1_000)
    deleted_snapshot_count: int = Field(ge=0)
    deleted_snapshot_ids: tuple[str, ...] = Field(default=(), max_length=1_000)
    expired_cache_entries: int = Field(ge=0)
    reporting_truncated: bool = False
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class IndexClearReport(IntelligenceModel):
    repository_id: str = Field(min_length=1, max_length=256)
    deleted_snapshot_count: int = Field(ge=0)
    status: IndexStatus
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class SymbolSummary(IntelligenceModel):
    symbol_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=512)
    qualified_name: str = Field(min_length=1, max_length=2_048)
    kind: SymbolKind
    language: SourceLanguage
    relative_path: str = Field(min_length=1, max_length=2_048)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    project_id: str | None = Field(default=None, max_length=256)
    module_id: str | None = Field(default=None, max_length=256)
    signature: str | None = Field(default=None, max_length=4_096)
    exported: bool = False
    test: bool = False
    endpoint: str | None = Field(default=None, max_length=2_048)
    confidence: float = Field(ge=0, le=1)


class SymbolQueryReport(IntelligenceModel):
    snapshot_id: str = Field(min_length=1, max_length=256)
    index_state: IndexState
    subject: str = Field(min_length=1, max_length=2_048)
    symbols: tuple[SymbolSummary, ...] = Field(
        default=(),
        max_length=_MAX_QUERY_RESULTS,
    )
    truncated: bool = False
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class SearchMatch(IntelligenceModel):
    rank: int = Field(ge=1)
    score: float = Field(ge=0)
    relative_path: str = Field(min_length=1, max_length=2_048)
    source_hash: str = Field(min_length=64, max_length=64)
    project_id: str | None = Field(default=None, max_length=256)
    symbol: SymbolSummary | None = None
    dependency_path: tuple[str, ...] = Field(default=(), max_length=64)
    stale: bool = False
    matched_terms: tuple[str, ...] = Field(default=(), max_length=128)
    score_components: dict[str, float] = Field(default_factory=dict)
    explanation: str = Field(min_length=1, max_length=2_048)


class RepositorySearchReport(IntelligenceModel):
    snapshot_id: str = Field(min_length=1, max_length=256)
    index_state: IndexState
    query: SearchQuery
    results: tuple[SearchMatch, ...] = Field(
        default=(),
        max_length=_MAX_QUERY_RESULTS,
    )
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class GraphNodeSummary(IntelligenceModel):
    node_id: str = Field(min_length=1, max_length=2_048)
    node_type: Literal["symbol", "file", "module", "unresolved"]
    label: str = Field(min_length=1, max_length=2_048)
    relative_path: str | None = Field(default=None, max_length=2_048)
    project_id: str | None = Field(default=None, max_length=256)
    language: SourceLanguage | None = None


class GraphEdgeSummary(IntelligenceModel):
    edge_id: str = Field(min_length=1, max_length=256)
    kind: DependencyKind
    source_id: str = Field(min_length=1, max_length=2_048)
    target_id: str = Field(min_length=1, max_length=2_048)
    relative_path: str | None = Field(default=None, max_length=2_048)
    confidence: float = Field(ge=0, le=1)
    resolved: bool
    explanation: str = Field(min_length=1, max_length=2_048)


class GraphQueryReport(IntelligenceModel):
    snapshot_id: str = Field(min_length=1, max_length=256)
    index_state: IndexState
    direction: Literal["dependencies", "dependents"]
    subject: SymbolSummary
    max_depth: int = Field(ge=0, le=16)
    nodes: tuple[GraphNodeSummary, ...] = Field(default=(), max_length=10_000)
    edges: tuple[GraphEdgeSummary, ...] = Field(default=(), max_length=100_000)
    maximum_depth_reached: int = Field(ge=0, le=16)
    truncated: bool = False
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class ContextCandidateSummary(IntelligenceModel):
    candidate_id: str = Field(min_length=1, max_length=256)
    relative_path: str = Field(min_length=1, max_length=2_048)
    source_hash: str = Field(min_length=64, max_length=64)
    symbol_id: str | None = Field(default=None, max_length=256)
    role: ContextRole
    score: float = Field(ge=0)
    byte_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    selected: bool = False
    reasons: tuple[str, ...] = Field(default=(), max_length=64)
    exclusion_reason: str | None = Field(default=None, max_length=1_024)


class ContextPlanSummary(IntelligenceModel):
    plan_id: str = Field(min_length=1, max_length=256)
    plan_hash: str = Field(min_length=64, max_length=64)
    snapshot_id: str | None = Field(default=None, max_length=256)
    role: ContextRole
    task_hash: str = Field(min_length=64, max_length=64)
    byte_budget: int = Field(gt=0)
    token_budget: int = Field(gt=0)
    selected_bytes: int = Field(ge=0)
    selected_tokens: int = Field(ge=0)
    candidates: tuple[ContextCandidateSummary, ...] = Field(
        default=(),
        max_length=2_000,
    )
    stale_warning: str | None = Field(default=None, max_length=2_048)
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


@dataclass(frozen=True)
class _RepositoryView:
    snapshot: IndexSnapshot
    status: IndexStatus
    projects: tuple[Project, ...]
    files: tuple[SourceFile, ...]
    modules: tuple[Module, ...]
    symbols: tuple[Symbol, ...]
    edges: tuple[DependencyEdge, ...]
    ownership_rules: tuple[OwnershipRule, ...]
    boundaries: tuple[ArchitectureBoundary, ...]


class RepositoryIntelligenceService:
    """Providerless facade over one contained workspace and local index store."""

    def __init__(
        self,
        workspace: str | Path,
        database_path: str | Path,
        *,
        repository_key: str | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise IndexUnavailableError(
                "Repository intelligence workspace is not a directory."
            )
        key = repository_key or local_repository_key(self.workspace)
        self.repository = repository_identity(key)
        self.workspace_identity = workspace_identity(
            self.repository.repository_id,
            ("",),
        )
        self.store = IndexStore(database_path)
        self.indexer = RepositoryIndexer(
            self.workspace,
            self.repository,
            self.workspace_identity,
            self.store,
        )
        self.freshness = IndexFreshnessChecker(
            self.workspace,
            self.repository,
            self.workspace_identity,
            self.store,
        )

    def build(self) -> IndexMutationReport:
        return self._mutation(IndexOperationKind.BUILD, self.indexer.build())

    def update(self) -> IndexMutationReport:
        return self._mutation(IndexOperationKind.UPDATE, self.indexer.update())

    def repair(self) -> IndexMutationReport:
        return self._mutation(IndexOperationKind.REPAIR, self.indexer.repair())

    def status(self) -> IndexStatus:
        return self.freshness.status()

    def verify(self) -> IndexVerificationReport:
        self.store.verify()
        status = self.status()
        return IndexVerificationReport(
            valid=status.state
            not in {
                IndexState.ABSENT,
                IndexState.CORRUPTED,
                IndexState.INCOMPATIBLE,
            },
            fresh=status.state == IndexState.CURRENT,
            schema_version=self.store.schema_version,
            status=status,
        )

    def overview(self) -> RepositoryOverview:
        view = self._view()
        files, modules, symbols, _edges = self._safe_records(view)
        file_counts = Counter(item.project_id for item in files)
        symbol_counts = Counter(item.project_id for item in symbols)
        project_languages: dict[str, set[SourceLanguage]] = {}
        for source in files:
            if source.project_id is not None:
                project_languages.setdefault(source.project_id, set()).add(
                    source.language
                )
        projects = tuple(
            ProjectSummary(
                project_id=item.project_id,
                name=item.name,
                kind=item.kind,
                root=item.root,
                source_roots=item.source_roots,
                test_roots=item.test_roots,
                file_count=file_counts[item.project_id],
                symbol_count=symbol_counts[item.project_id],
                languages=tuple(
                    sorted(
                        project_languages.get(item.project_id, set()),
                        key=lambda value: value.value,
                    )
                ),
            )
            for item in sorted(view.projects, key=lambda value: value.project_id)[
                :_MAX_OVERVIEW_PROJECTS
            ]
        )
        language_files = Counter(item.language for item in files)
        language_symbols = Counter(item.language for item in symbols)
        languages = tuple(
            LanguageSummary(
                language=language,
                file_count=language_files[language],
                symbol_count=language_symbols[language],
            )
            for language in sorted(
                set(language_files) | set(language_symbols),
                key=lambda value: value.value,
            )
        )
        module_symbols = Counter(item.module_id for item in symbols)
        module_summaries = tuple(
            ModuleSummary(
                module_id=item.module_id,
                project_id=item.project_id,
                name=item.name,
                qualified_name=item.qualified_name,
                relative_path=item.relative_path,
                language=item.language,
                public=item.public,
                symbol_count=module_symbols[item.module_id],
            )
            for item in sorted(modules, key=lambda value: value.module_id)[
                :_MAX_OVERVIEW_MODULES
            ]
        )
        ownership = tuple(
            OwnershipRuleSummary(
                rule_id=item.rule_id,
                pattern=item.pattern,
                owners=item.owners,
                source_path=item.source_path,
                confidence=item.confidence,
                explanation=item.explanation,
            )
            for item in sorted(view.ownership_rules, key=lambda value: value.rule_id)[
                :_MAX_OVERVIEW_RULES
            ]
        )
        boundaries = tuple(
            ArchitectureBoundarySummary(
                boundary_id=item.boundary_id,
                name=item.name,
                boundary_type=item.boundary_type,
                scope=item.scope,
                confidence=item.confidence,
                explanation=item.explanation,
                forbidden_targets=item.forbidden_targets,
            )
            for item in sorted(view.boundaries, key=lambda value: value.boundary_id)[
                :_MAX_OVERVIEW_RULES
            ]
        )
        return RepositoryOverview(
            snapshot_id=view.snapshot.snapshot_id,
            index_state=view.status.state,
            projects=projects,
            languages=languages,
            modules=module_summaries,
            symbol_kind_counts={
                kind.value: count
                for kind, count in sorted(
                    Counter(item.kind for item in symbols).items(),
                    key=lambda item: item[0].value,
                )
            },
            ownership_rules=ownership,
            architecture_boundaries=boundaries,
            truncated=any(
                (
                    len(view.projects) > _MAX_OVERVIEW_PROJECTS,
                    len(modules) > _MAX_OVERVIEW_MODULES,
                    len(view.ownership_rules) > _MAX_OVERVIEW_RULES,
                    len(view.boundaries) > _MAX_OVERVIEW_RULES,
                )
            ),
        )

    def clear(self) -> IndexClearReport:
        deleted = self.store.clear_repository(self.repository.repository_id)
        return IndexClearReport(
            repository_id=self.repository.repository_id,
            deleted_snapshot_count=deleted,
            status=self.status(),
        )

    def garbage_collect(
        self,
        *,
        retain: int = 3,
        now: datetime | None = None,
    ) -> IndexGarbageCollectionReport:
        deleted = self.store.prune_snapshots(
            self.repository.repository_id,
            retain=retain,
        )
        expired = self.store.purge_expired_cache(
            now=now or datetime.now(timezone.utc),
            repository_id=self.repository.repository_id,
        )
        return IndexGarbageCollectionReport(
            retained_snapshots=retain,
            deleted_snapshot_count=len(deleted),
            deleted_snapshot_ids=deleted[:1_000],
            expired_cache_entries=expired,
            reporting_truncated=len(deleted) > 1_000,
        )

    def search(
        self,
        text: str,
        *,
        projects: Iterable[str] = (),
        languages: Iterable[SourceLanguage | str] = (),
        symbol_kinds: Iterable[SymbolKind | str] = (),
        path_prefixes: Iterable[str] = (),
        test_only: bool = False,
        limit: int = 25,
        offset: int = 0,
    ) -> RepositorySearchReport:
        view = self._view()
        query = SearchQuery(
            text=text,
            project_ids=self._project_ids(view, projects),
            languages=tuple(SourceLanguage(item) for item in languages),
            symbol_kinds=tuple(SymbolKind(item) for item in symbol_kinds),
            path_prefixes=tuple(path_prefixes),
            test_only=test_only,
            limit=limit,
            offset=offset,
        )
        results = self._retriever(view).search(
            query,
            stale=view.status.state != IndexState.CURRENT,
        )
        return RepositorySearchReport(
            snapshot_id=view.snapshot.snapshot_id,
            index_state=view.status.state,
            query=query,
            results=tuple(
                SearchMatch(
                    rank=item.rank,
                    score=item.score,
                    relative_path=item.relative_path,
                    source_hash=item.source_hash,
                    project_id=item.project_id,
                    symbol=(
                        _symbol_summary(item.symbol)
                        if item.symbol is not None
                        else None
                    ),
                    dependency_path=item.dependency_path,
                    stale=item.stale,
                    matched_terms=item.matched_terms,
                    score_components=item.score_components,
                    explanation=item.explanation,
                )
                for item in results
            ),
        )

    def symbols(
        self,
        subject: str,
        *,
        projects: Iterable[str] = (),
        languages: Iterable[SourceLanguage | str] = (),
        limit: int = 50,
    ) -> SymbolQueryReport:
        if limit < 1 or limit > _MAX_QUERY_RESULTS:
            raise ValueError("symbol query limit must be between 1 and 200")
        if not subject.strip() or len(subject) > 2_048:
            raise RepositoryQueryError(
                "A file path, symbol name, or symbol identity is required."
            )
        view = self._view()
        ranked = self._matching_symbols(
            view,
            subject,
            projects=projects,
            languages=languages,
        )
        selected = ranked[:limit]
        return SymbolQueryReport(
            snapshot_id=view.snapshot.snapshot_id,
            index_state=view.status.state,
            subject=subject.strip(),
            symbols=tuple(_symbol_summary(item) for _, item in selected),
            truncated=len(ranked) > len(selected),
        )

    def dependencies(
        self,
        subject: str,
        *,
        direction: Literal["dependencies", "dependents"] = "dependencies",
        max_depth: int = 1,
        projects: Iterable[str] = (),
        languages: Iterable[SourceLanguage | str] = (),
        include_unresolved: bool = False,
    ) -> GraphQueryReport:
        if max_depth < 0 or max_depth > 16:
            raise ValueError("graph depth must be between 0 and 16")
        if direction not in {"dependencies", "dependents"}:
            raise ValueError("graph direction must be dependencies or dependents")
        view = self._view()
        symbol = self._resolve_symbol(
            view,
            subject,
            projects=projects,
            languages=languages,
        )
        files, modules, symbols, edges = self._safe_records(view)
        graph = DependencyGraph(
            edges,
            files=files,
            modules=modules,
            symbols=symbols,
        )
        if max_depth == 0:
            traversal = TraversalResult(
                node_ids=(symbol.symbol_id,),
                edges=(),
                maximum_depth_reached=0,
            )
        elif direction == "dependencies":
            traversal = graph.transitive_dependencies(
                symbol.symbol_id,
                max_depth=max_depth,
                include_unresolved=include_unresolved,
            )
        else:
            traversal = graph.transitive_dependents(
                symbol.symbol_id,
                max_depth=max_depth,
                include_unresolved=include_unresolved,
            )
        nodes = _graph_nodes(
            traversal.node_ids,
            files=files,
            modules=modules,
            symbols=symbols,
        )
        return GraphQueryReport(
            snapshot_id=view.snapshot.snapshot_id,
            index_state=view.status.state,
            direction=direction,
            subject=_symbol_summary(symbol),
            max_depth=max_depth,
            nodes=nodes,
            edges=tuple(_edge_summary(item) for item in traversal.edges),
            maximum_depth_reached=traversal.maximum_depth_reached,
            truncated=traversal.truncated,
        )

    def impact(
        self,
        subjects: Iterable[str],
        *,
        max_depth: int = 4,
        max_nodes: int = 500,
        projects: Iterable[str] = (),
        languages: Iterable[SourceLanguage | str] = (),
    ) -> ImpactResult:
        view = self._view()
        request = self._impact_request(
            view,
            subjects,
            max_depth=max_depth,
            max_nodes=max_nodes,
            projects=projects,
            languages=languages,
        )
        files, modules, symbols, edges = self._safe_records(view)
        graph = DependencyGraph(
            edges,
            files=files,
            modules=modules,
            symbols=symbols,
        )
        selector = TestImpactSelector(
            graph,
            projects=view.projects,
            files=files,
            symbols=symbols,
        )
        analyzer = ChangeImpactAnalyzer(
            graph,
            projects=view.projects,
            files=files,
            symbols=symbols,
            boundaries=view.boundaries,
            ownership=view.ownership_rules,
            test_selector=selector,
        )
        return analyzer.analyze(request, snapshot_id=view.snapshot.snapshot_id)

    def tests_for(
        self,
        subjects: Iterable[str],
        *,
        max_depth: int = 4,
        max_nodes: int = 500,
        projects: Iterable[str] = (),
        languages: Iterable[SourceLanguage | str] = (),
    ) -> TestImpactResult:
        return self.impact(
            subjects,
            max_depth=max_depth,
            max_nodes=max_nodes,
            projects=projects,
            languages=languages,
        ).tests

    def context_plan(
        self,
        task: str,
        *,
        role: ContextRole | str = ContextRole.PLANNER,
        byte_budget: int = 100_000,
        token_budget: int = 16_000,
        projects: Iterable[str] = (),
        changed_paths: Iterable[str] = (),
    ) -> ContextPlanSummary:
        view = self._view()
        files, _, symbols, _ = self._safe_records(view)
        inventory = RepositoryInventoryScanner(self.workspace).scan()
        planner = ContextPlanner(
            inventory,
            self._retriever(view),
            files,
            symbols,
        )
        plan = planner.plan(
            ContextPlanningRequest(
                task=task,
                role=ContextRole(role),
                byte_budget=byte_budget,
                token_budget=token_budget,
                snapshot_id=view.snapshot.snapshot_id,
                index_state=view.status.state,
                project_ids=self._project_ids(view, projects),
                changed_paths=tuple(changed_paths),
            )
        )
        return summarize_context_plan(plan)

    def _mutation(
        self,
        operation: IndexOperationKind,
        result: IndexingResult,
    ) -> IndexMutationReport:
        path_groups = (
            result.indexed_paths,
            result.reused_paths,
            result.skipped_paths,
            result.deleted_paths,
            result.invalidated_paths,
        )
        return IndexMutationReport(
            operation=operation,
            snapshot=result.snapshot,
            status=self.status(),
            operation_id=(
                result.operation.operation_id
                if result.operation is not None
                else None
            ),
            unchanged=result.unchanged,
            indexed_count=len(result.indexed_paths),
            reused_count=len(result.reused_paths),
            skipped_count=len(result.skipped_paths),
            deleted_count=len(result.deleted_paths),
            renamed_count=len(result.renamed_paths),
            invalidated_count=len(result.invalidated_paths),
            indexed_paths=result.indexed_paths[:_MAX_MAINTENANCE_PATHS],
            reused_paths=result.reused_paths[:_MAX_MAINTENANCE_PATHS],
            skipped_paths=result.skipped_paths[:_MAX_MAINTENANCE_PATHS],
            deleted_paths=result.deleted_paths[:_MAX_MAINTENANCE_PATHS],
            invalidated_paths=result.invalidated_paths[:_MAX_MAINTENANCE_PATHS],
            path_reporting_truncated=any(
                len(items) > _MAX_MAINTENANCE_PATHS for items in path_groups
            ),
        )

    def _view(self) -> _RepositoryView:
        status = self.status()
        if status.state in {IndexState.CORRUPTED, IndexState.INCOMPATIBLE}:
            raise IndexUnavailableError(
                "Repository index must be repaired before it can be queried."
            )
        if status.snapshot_id is None:
            raise IndexUnavailableError(
                "Repository index is absent; run 'agentbus index build'."
            )
        snapshot = self.store.get_snapshot(status.snapshot_id)
        return _RepositoryView(
            snapshot=snapshot,
            status=status,
            projects=self.store.list_projects(snapshot.snapshot_id),
            files=self.store.list_files(snapshot.snapshot_id),
            modules=self.store.list_modules(snapshot.snapshot_id),
            symbols=self.store.list_symbols(snapshot.snapshot_id),
            edges=self.store.list_edges(snapshot.snapshot_id),
            ownership_rules=self.store.list_ownership_rules(snapshot.snapshot_id),
            boundaries=self.store.list_architecture_boundaries(snapshot.snapshot_id),
        )

    def _safe_records(
        self,
        view: _RepositoryView,
    ) -> tuple[
        tuple[SourceFile, ...],
        tuple[Module, ...],
        tuple[Symbol, ...],
        tuple[DependencyEdge, ...],
    ]:
        protected_files = {item.file_id for item in view.files if item.protected}
        protected_paths = {
            item.relative_path for item in view.files if item.protected
        }
        protected_symbols = {
            item.symbol_id
            for item in view.symbols
            if item.file_id in protected_files
        }
        protected_modules = {
            item.module_id
            for item in view.modules
            if item.relative_path in protected_paths
        }
        protected_nodes = protected_files | protected_symbols | protected_modules
        return (
            tuple(item for item in view.files if item.file_id not in protected_files),
            tuple(
                item for item in view.modules if item.module_id not in protected_modules
            ),
            tuple(
                item for item in view.symbols if item.symbol_id not in protected_symbols
            ),
            tuple(
                item
                for item in view.edges
                if item.source_id not in protected_nodes
                and item.target_id not in protected_nodes
            ),
        )

    def _retriever(self, view: _RepositoryView) -> HybridRetriever:
        files, modules, symbols, edges = self._safe_records(view)
        graph = DependencyGraph(
            edges,
            files=files,
            modules=modules,
            symbols=symbols,
        )
        lexical = RepositoryLexicalIndex(
            view.projects,
            files,
            modules,
            symbols,
            snapshot_id=view.snapshot.snapshot_id,
        )
        return HybridRetriever(
            lexical,
            graph,
            files,
            symbols,
            boundaries=view.boundaries,
        )

    def _project_ids(
        self,
        view: _RepositoryView,
        filters: Iterable[str],
    ) -> tuple[str, ...]:
        selected: set[str] = set()
        for raw in filters:
            value = str(raw).strip()
            if not value or len(value) > 2_048:
                raise RepositoryQueryError("Project filter is empty or too long.")
            folded = value.replace("\\", "/").casefold().strip("/")
            matches = {
                project.project_id
                for project in view.projects
                if value == project.project_id
                or folded == project.name.casefold()
                or folded == project.root.casefold().strip("/")
            }
            if not matches:
                raise RepositoryQueryError(
                    "Project filter did not match the selected index snapshot."
                )
            selected.update(matches)
        return tuple(sorted(selected))

    def _resolve_symbol(
        self,
        view: _RepositoryView,
        subject: str,
        *,
        projects: Iterable[str],
        languages: Iterable[SourceLanguage | str],
    ) -> Symbol:
        ranked = self._matching_symbols(
            view,
            subject,
            projects=projects,
            languages=languages,
        )
        if not ranked:
            raise RepositoryQueryError(
                "Symbol was not found in the selected index snapshot."
            )
        folded = subject.strip().casefold()
        exact = tuple(
            item
            for _, item in ranked
            if folded
            in {
                item.symbol_id.casefold(),
                item.name.casefold(),
                item.qualified_name.casefold(),
            }
        )
        candidates = exact or tuple(item for _, item in ranked)
        if len(candidates) != 1:
            raise RepositoryQueryError(
                "Symbol query is ambiguous; use the stable symbol identity."
            )
        return candidates[0]

    def _matching_symbols(
        self,
        view: _RepositoryView,
        subject: str,
        *,
        projects: Iterable[str],
        languages: Iterable[SourceLanguage | str],
    ) -> list[tuple[int, Symbol]]:
        project_ids = set(self._project_ids(view, projects))
        language_values = {SourceLanguage(item) for item in languages}
        safe_symbols = self._safe_records(view)[2]
        needle = subject.strip().replace("\\", "/")
        folded = needle.casefold()
        normalized_path = _optional_relative_path(needle)
        ranked: list[tuple[int, Symbol]] = []
        for symbol in safe_symbols:
            if project_ids and symbol.project_id not in project_ids:
                continue
            if language_values and symbol.language not in language_values:
                continue
            values = {
                symbol.symbol_id.casefold(),
                symbol.name.casefold(),
                symbol.qualified_name.casefold(),
            }
            path = symbol.location.relative_path
            if folded == symbol.symbol_id.casefold():
                rank = 0
            elif normalized_path is not None and normalized_path == path:
                rank = 1
            elif folded in {
                symbol.name.casefold(),
                symbol.qualified_name.casefold(),
            }:
                rank = 2
            elif any(folded in value for value in values) or folded in path.casefold():
                rank = 3
            else:
                continue
            ranked.append((rank, symbol))
        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].location.relative_path.casefold(),
                item[1].qualified_name.casefold(),
                item[1].symbol_id,
            )
        )
        return ranked

    def _impact_request(
        self,
        view: _RepositoryView,
        subjects: Iterable[str],
        *,
        max_depth: int,
        max_nodes: int,
        projects: Iterable[str],
        languages: Iterable[SourceLanguage | str],
    ) -> ImpactRequest:
        values = tuple(subjects)
        if not values or len(values) > 1_000:
            raise RepositoryQueryError(
                "Impact analysis requires between 1 and 1000 subjects."
            )
        files, _, symbols, _ = self._safe_records(view)
        files_by_path = {item.relative_path: item for item in files}
        selected_projects = set(self._project_ids(view, projects))
        selected_languages = {SourceLanguage(item) for item in languages}
        paths: set[str] = set()
        symbol_ids: set[str] = set()
        for raw in values:
            subject = str(raw).strip()
            if not subject or len(subject) > 2_048:
                raise RepositoryQueryError("Impact subject is empty or too long.")
            if subject.startswith("path:"):
                path = _relative_path(subject[5:])
                self._validate_subject_file(
                    files_by_path.get(path),
                    selected_projects,
                    selected_languages,
                )
                paths.add(path)
                continue
            if subject.startswith("symbol:"):
                symbol = self._resolve_symbol(
                    view,
                    subject[7:],
                    projects=projects,
                    languages=languages,
                )
                symbol_ids.add(symbol.symbol_id)
                continue
            if subject.startswith("symbol_"):
                symbol = self._resolve_symbol(
                    view,
                    subject,
                    projects=projects,
                    languages=languages,
                )
                symbol_ids.add(symbol.symbol_id)
                continue
            path = _optional_relative_path(subject)
            if path is not None and path in files_by_path:
                self._validate_subject_file(
                    files_by_path[path],
                    selected_projects,
                    selected_languages,
                )
                paths.add(path)
                continue
            exact_symbols = tuple(
                item
                for item in symbols
                if subject.casefold()
                in {item.name.casefold(), item.qualified_name.casefold()}
                and (not selected_projects or item.project_id in selected_projects)
                and (not selected_languages or item.language in selected_languages)
            )
            if len(exact_symbols) == 1:
                symbol_ids.add(exact_symbols[0].symbol_id)
                continue
            if len(exact_symbols) > 1:
                raise RepositoryQueryError(
                    "Impact symbol is ambiguous; use the stable symbol identity."
                )
            if path is not None and (
                "/" in path or bool(PurePosixPath(path).suffix)
            ):
                paths.add(path)
                continue
            raise RepositoryQueryError(
                "Impact subject was not found; use path: or symbol: explicitly."
            )
        return ImpactRequest(
            paths=tuple(sorted(paths)),
            symbol_ids=tuple(sorted(symbol_ids)),
            max_depth=max_depth,
            max_nodes=max_nodes,
        )

    @staticmethod
    def _validate_subject_file(
        source: SourceFile | None,
        projects: set[str],
        languages: set[SourceLanguage],
    ) -> None:
        if source is None:
            return
        if projects and source.project_id not in projects:
            raise RepositoryQueryError(
                "Impact path is outside the selected project filter."
            )
        if languages and source.language not in languages:
            raise RepositoryQueryError(
                "Impact path is outside the selected language filter."
            )


def local_repository_key(workspace: str | Path) -> str:
    """Create a local-only key without persisting a personal absolute path."""

    resolved = Path(workspace).expanduser().resolve()
    normalized = os.path.normcase(str(resolved))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"local/{digest}"


def summarize_context_plan(plan: ContextPlan) -> ContextPlanSummary:
    return ContextPlanSummary(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        snapshot_id=plan.snapshot_id,
        role=plan.role,
        task_hash=plan.task_hash,
        byte_budget=plan.byte_budget,
        token_budget=plan.token_budget,
        selected_bytes=plan.selected_bytes,
        selected_tokens=plan.selected_tokens,
        candidates=tuple(
            ContextCandidateSummary(
                candidate_id=item.candidate_id,
                relative_path=item.relative_path,
                source_hash=item.source_hash,
                symbol_id=item.symbol_id,
                role=item.role,
                score=item.score,
                byte_count=item.byte_count,
                estimated_tokens=item.estimated_tokens,
                selected=item.selected,
                reasons=item.reasons,
                exclusion_reason=item.exclusion_reason,
            )
            for item in plan.candidates
        ),
        stale_warning=plan.stale_warning,
    )


def _symbol_summary(symbol: Symbol) -> SymbolSummary:
    return SymbolSummary(
        symbol_id=symbol.symbol_id,
        name=symbol.name,
        qualified_name=symbol.qualified_name,
        kind=symbol.kind,
        language=symbol.language,
        relative_path=symbol.location.relative_path,
        start_line=symbol.location.start_line,
        end_line=symbol.location.end_line,
        project_id=symbol.project_id,
        module_id=symbol.module_id,
        signature=symbol.signature,
        exported=symbol.exported,
        test=symbol.test,
        endpoint=symbol.endpoint,
        confidence=symbol.confidence,
    )


def _edge_summary(edge: DependencyEdge) -> GraphEdgeSummary:
    return GraphEdgeSummary(
        edge_id=edge.edge_id,
        kind=edge.kind,
        source_id=edge.source_id,
        target_id=edge.target_id,
        relative_path=(
            edge.location.relative_path if edge.location is not None else None
        ),
        confidence=edge.confidence,
        resolved=edge.resolved,
        explanation=edge.explanation,
    )


def _graph_nodes(
    node_ids: Iterable[str],
    *,
    files: Iterable[SourceFile],
    modules: Iterable[Module],
    symbols: Iterable[Symbol],
) -> tuple[GraphNodeSummary, ...]:
    file_map = {item.file_id: item for item in files}
    module_map = {item.module_id: item for item in modules}
    symbol_map = {item.symbol_id: item for item in symbols}
    records: list[GraphNodeSummary] = []
    for node_id in tuple(dict.fromkeys(node_ids)):
        if node_id in symbol_map:
            symbol = symbol_map[node_id]
            records.append(
                GraphNodeSummary(
                    node_id=node_id,
                    node_type="symbol",
                    label=symbol.qualified_name,
                    relative_path=symbol.location.relative_path,
                    project_id=symbol.project_id,
                    language=symbol.language,
                )
            )
        elif node_id in file_map:
            source = file_map[node_id]
            records.append(
                GraphNodeSummary(
                    node_id=node_id,
                    node_type="file",
                    label=source.relative_path,
                    relative_path=source.relative_path,
                    project_id=source.project_id,
                    language=source.language,
                )
            )
        elif node_id in module_map:
            module = module_map[node_id]
            records.append(
                GraphNodeSummary(
                    node_id=node_id,
                    node_type="module",
                    label=module.qualified_name,
                    relative_path=module.relative_path,
                    project_id=module.project_id,
                    language=module.language,
                )
            )
        else:
            records.append(
                GraphNodeSummary(
                    node_id=node_id,
                    node_type="unresolved",
                    label=node_id,
                )
            )
    return tuple(records)


def _optional_relative_path(value: str) -> str | None:
    try:
        return _relative_path(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "ArchitectureBoundarySummary",
    "ContextCandidateSummary",
    "ContextPlanSummary",
    "GraphEdgeSummary",
    "GraphNodeSummary",
    "GraphQueryReport",
    "IndexClearReport",
    "IndexGarbageCollectionReport",
    "IndexMutationReport",
    "IndexVerificationReport",
    "LanguageSummary",
    "ModuleSummary",
    "OwnershipRuleSummary",
    "ProjectSummary",
    "RepositoryOverview",
    "RepositoryIntelligenceService",
    "RepositorySearchReport",
    "SearchMatch",
    "SymbolQueryReport",
    "SymbolSummary",
    "local_repository_key",
    "summarize_context_plan",
]
