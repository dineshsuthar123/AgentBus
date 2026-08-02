from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Literal

from pydantic import Field, field_validator, model_validator

from agentbus.trace.models import Sha256Digest, TraceModel
from agentbus.trace.redaction import (
    canonical_json_bytes,
    sanitize_document,
    sanitize_text,
)

if TYPE_CHECKING:
    from agentbus.intelligence.models import (
        ArchitectureBoundary,
        ImpactResult,
        TestImpactResult,
    )
    from agentbus.runtime.intelligence import PlannerIntelligenceContext


REPOSITORY_INTELLIGENCE_COMPONENT = "repository_intelligence"
REPOSITORY_INTELLIGENCE_EVIDENCE_SCHEMA_VERSION = 1
REPOSITORY_INTELLIGENCE_EVIDENCE_MEDIA_TYPE = (
    "application/vnd.agentbus.repository-intelligence-evidence+json"
)
_MAX_RETRIEVAL_RESULTS = 256
_MAX_IMPACT_NODES = 4_000
_MAX_IMPACT_EVIDENCE = 2_000


class IntelligenceDriftCategory(str, Enum):
    CURRENT_INDEX_UNAVAILABLE = "current_index_unavailable"
    INDEX_SNAPSHOT = "index_snapshot_drift"
    PARSER_VERSIONS = "parser_version_drift"
    PROJECT_MAP = "project_map_drift"
    GRAPH = "graph_drift"
    GRAPH_RESULTS = "graph_result_drift"
    ARCHITECTURE = "architecture_drift"
    RETRIEVAL_RESULTS = "retrieval_result_drift"
    RETRIEVAL_SCORING = "retrieval_scoring_drift"
    CONTEXT_PLAN = "context_plan_drift"
    IMPACT = "impact_result_drift"
    TEST_SELECTION = "test_selection_drift"


class IndexSnapshotTraceEvidence(TraceModel):
    snapshot_id: str = Field(min_length=1, max_length=256)
    repository_id: str = Field(min_length=1, max_length=256)
    workspace_id: str = Field(min_length=1, max_length=256)
    state: str = Field(min_length=1, max_length=64)
    project_map_hash: Sha256Digest
    graph_hash: Sha256Digest
    source_fingerprint: Sha256Digest
    parser_versions: dict[str, str] = Field(default_factory=dict)

    @field_validator("parser_versions")
    @classmethod
    def parser_versions_are_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 64:
            raise ValueError("trace parser version map exceeds its bound")
        return dict(sorted(value.items()))


class RetrievalTraceEvidence(TraceModel):
    result_hash: Sha256Digest
    candidate_id: str = Field(min_length=1, max_length=256)
    relative_path: str = Field(min_length=1, max_length=2_048)
    source_hash: Sha256Digest
    symbol_id: str | None = Field(default=None, max_length=256)
    score: float = Field(ge=0)
    scoring_explanations: tuple[str, ...] = Field(default=(), max_length=64)
    selected: bool = False

    @model_validator(mode="after")
    def validate_result_hash(self) -> RetrievalTraceEvidence:
        expected = _document_hash(
            {
                "candidate_id": self.candidate_id,
                "relative_path": self.relative_path,
                "source_hash": self.source_hash,
                "symbol_id": self.symbol_id,
            }
        )
        if self.result_hash != expected:
            raise ValueError("repository retrieval result hash does not match")
        return self


class ArchitectureBoundaryTraceEvidence(TraceModel):
    boundary_id: str = Field(min_length=1, max_length=256)
    boundary_type: str = Field(min_length=1, max_length=64)
    scope: tuple[str, ...] = Field(default=(), max_length=256)
    forbidden_targets: tuple[str, ...] = Field(default=(), max_length=256)
    confidence: float = Field(ge=0, le=1)
    boundary_hash: Sha256Digest

    @model_validator(mode="after")
    def validate_boundary_hash(self) -> ArchitectureBoundaryTraceEvidence:
        expected = _document_hash(
            self.model_dump(mode="json", exclude={"boundary_hash"})
        )
        if self.boundary_hash != expected:
            raise ValueError("repository architecture boundary hash does not match")
        return self


class ImpactTraceEvidence(TraceModel):
    result_id: str = Field(min_length=1, max_length=256)
    snapshot_id: str | None = Field(default=None, max_length=256)
    result_hash: Sha256Digest
    risk: str = Field(min_length=1, max_length=64)
    confidence: float = Field(ge=0, le=1)
    changed_paths: tuple[str, ...] = Field(default=(), max_length=1_000)
    changed_symbols: tuple[str, ...] = Field(default=(), max_length=1_000)
    direct_dependents: tuple[str, ...] = Field(default=(), max_length=_MAX_IMPACT_NODES)
    transitive_dependents: tuple[str, ...] = Field(
        default=(),
        max_length=_MAX_IMPACT_NODES,
    )
    affected_projects: tuple[str, ...] = Field(default=(), max_length=1_000)
    affected_public_apis: tuple[str, ...] = Field(default=(), max_length=2_000)
    affected_endpoints: tuple[str, ...] = Field(default=(), max_length=2_000)
    affected_configurations: tuple[str, ...] = Field(
        default=(),
        max_length=2_000,
    )
    architecture_crossings: tuple[str, ...] = Field(
        default=(),
        max_length=2_000,
    )
    ownership_rules: tuple[str, ...] = Field(default=(), max_length=1_000)
    integration_hotspots: tuple[str, ...] = Field(default=(), max_length=2_000)
    uncertainty: tuple[str, ...] = Field(default=(), max_length=256)
    evidence_hashes: tuple[Sha256Digest, ...] = Field(
        default=(),
        max_length=_MAX_IMPACT_EVIDENCE,
    )
    source_truncated: bool = False


class TestSelectionTraceEvidence(TraceModel):
    result_id: str = Field(min_length=1, max_length=256)
    result_hash: Sha256Digest
    selected_tests: tuple[str, ...] = Field(default=(), max_length=2_000)
    mandatory_tests: tuple[str, ...] = Field(default=(), max_length=2_000)
    optional_tests: tuple[str, ...] = Field(default=(), max_length=2_000)
    full_suite_recommended: bool = False
    confidence: float = Field(ge=0, le=1)
    evidence_hashes: tuple[Sha256Digest, ...] = Field(default=(), max_length=2_000)
    escalation_reasons: tuple[str, ...] = Field(default=(), max_length=256)


class RepositoryIntelligenceTraceEvidence(TraceModel):
    schema_version: int = Field(
        default=REPOSITORY_INTELLIGENCE_EVIDENCE_SCHEMA_VERSION,
        ge=1,
    )
    search_query: str = Field(min_length=1, max_length=2_048)
    search_query_sha256: Sha256Digest
    context_hash: Sha256Digest
    snapshot: IndexSnapshotTraceEvidence | None = None
    retrieval_results: tuple[RetrievalTraceEvidence, ...] = Field(
        default=(),
        max_length=_MAX_RETRIEVAL_RESULTS,
    )
    retrieval_result_hashes: tuple[Sha256Digest, ...] = Field(
        default=(),
        max_length=_MAX_RETRIEVAL_RESULTS,
    )
    retrieval_scoring_sha256: Sha256Digest
    retrieval_truncated: bool = False
    context_plan_id: str | None = Field(default=None, max_length=256)
    context_plan_hash: Sha256Digest | None = None
    dependency_result_hash: Sha256Digest
    architecture_result_hash: Sha256Digest
    architecture_boundaries: tuple[ArchitectureBoundaryTraceEvidence, ...] = Field(
        default=(),
        max_length=256,
    )
    impact_result: ImpactTraceEvidence | None = None
    test_selection_result: TestSelectionTraceEvidence | None = None
    protected_items_omitted: int = Field(default=0, ge=0)
    redaction_reasons: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_derived_hashes(self) -> RepositoryIntelligenceTraceEvidence:
        if self.schema_version != REPOSITORY_INTELLIGENCE_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported repository intelligence evidence schema")
        if self.search_query_sha256 != _document_hash(self.search_query):
            raise ValueError("repository intelligence query hash does not match")
        expected_results = tuple(item.result_hash for item in self.retrieval_results)
        if self.retrieval_result_hashes != expected_results:
            raise ValueError("repository intelligence retrieval hashes do not match")
        expected_scoring = _retrieval_scoring_hash(self.retrieval_results)
        if self.retrieval_scoring_sha256 != expected_scoring:
            raise ValueError("repository intelligence scoring hash does not match")
        expected_architecture = _document_hash(
            [item.model_dump(mode="json") for item in self.architecture_boundaries]
        )
        if self.architecture_result_hash != expected_architecture:
            raise ValueError("repository architecture result hash does not match")
        return self


class IntelligenceDriftFinding(TraceModel):
    category: IntelligenceDriftCategory
    historical_sha256: Sha256Digest | None = None
    current_sha256: Sha256Digest | None = None
    summary: str = Field(min_length=1, max_length=1_000)


class RepositoryIntelligenceReplayReport(TraceModel):
    captured_context_hash: Sha256Digest
    current_context_hash: Sha256Digest | None = None
    captured_snapshot_id: str | None = Field(default=None, max_length=256)
    current_snapshot_id: str | None = Field(default=None, max_length=256)
    captured_evidence_reused: bool = True
    captured_snapshot_reused: bool = False
    compared_current: bool = False
    index_drift: bool = False
    retrieval_drift: bool = False
    findings: tuple[IntelligenceDriftFinding, ...] = Field(
        default=(),
        max_length=32,
    )
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


def build_repository_intelligence_trace_evidence(
    search_query: str,
    context: PlannerIntelligenceContext,
    *,
    private_roots: Iterable[str | Path] = (),
) -> RepositoryIntelligenceTraceEvidence:
    roots = tuple(private_roots)
    query_document = sanitize_text(
        search_query,
        private_roots=roots,
        max_chars=2_000,
    )
    safe_query = str(query_document.value).strip() or "[EMPTY_QUERY]"
    protected_paths = {
        item.relative_path.casefold() for item in context.files if item.protected
    }
    protected_ids = {item.file_id for item in context.files if item.protected}
    protected_ids.update(
        item.symbol_id for item in context.symbols if item.file_id in protected_ids
    )
    protected_items_omitted = 0

    retrieval_results: list[RetrievalTraceEvidence] = []
    candidates = context.context_plan.candidates if context.context_plan else ()
    for candidate in sorted(
        candidates,
        key=lambda item: (
            not item.selected,
            -item.score,
            item.relative_path.casefold(),
            item.symbol_id or "",
            item.candidate_id,
        ),
    ):
        if (
            candidate.relative_path.casefold() in protected_paths
            or candidate.symbol_id in protected_ids
        ):
            protected_items_omitted += 1
            continue
        if len(retrieval_results) >= _MAX_RETRIEVAL_RESULTS:
            continue
        identity_payload = {
            "candidate_id": _safe_text(candidate.candidate_id, roots, 256),
            "relative_path": _safe_text(candidate.relative_path, roots, 2_000),
            "source_hash": candidate.source_hash,
            "symbol_id": (
                _safe_text(candidate.symbol_id, roots, 256)
                if candidate.symbol_id
                else None
            ),
        }
        payload = {
            **identity_payload,
            "score": candidate.score,
            "scoring_explanations": _safe_values(
                candidate.reasons,
                roots,
                maximum=64,
                maximum_characters=256,
            ),
            "selected": candidate.selected,
        }
        retrieval_results.append(
            RetrievalTraceEvidence(
                result_hash=_document_hash(identity_payload),
                **payload,
            )
        )
    safe_candidate_count = len(candidates) - protected_items_omitted
    retrieval_truncated = safe_candidate_count > len(retrieval_results)

    safe_dependencies = [
        {
            "node_ids": item.node_ids,
            "edge_ids": item.edge_ids,
            "confidence": item.confidence,
        }
        for item in context.dependency_paths
        if not protected_ids.intersection(item.node_ids)
    ]
    safe_dependencies.sort(
        key=lambda item: (
            item["node_ids"],
            item["edge_ids"],
            item["confidence"],
        )
    )

    boundaries: list[ArchitectureBoundaryTraceEvidence] = []
    for boundary in sorted(context.boundaries, key=lambda item: item.boundary_id):
        prepared = _boundary_evidence(
            boundary,
            protected_paths=protected_paths,
            private_roots=roots,
        )
        if prepared is None:
            protected_items_omitted += 1
            continue
        boundaries.append(prepared)
        if len(boundaries) >= 256:
            break

    snapshot = None
    if context.snapshot is not None:
        snapshot = IndexSnapshotTraceEvidence(
            snapshot_id=_safe_text(context.snapshot.snapshot_id, roots, 256),
            repository_id=_safe_text(context.snapshot.repository_id, roots, 256),
            workspace_id=_safe_text(context.snapshot.workspace_id, roots, 256),
            state=context.snapshot.state.value,
            project_map_hash=context.snapshot.project_map_hash,
            graph_hash=context.snapshot.graph_hash,
            source_fingerprint=context.snapshot.source_fingerprint,
            parser_versions={
                _safe_text(name, roots, 128): _safe_text(version, roots, 128)
                for name, version in sorted(context.snapshot.parser_versions.items())
            },
        )

    impact = (
        _impact_evidence(context.impact, roots)
        if context.impact is not None
        else None
    )
    tests = (
        _test_selection_evidence(context.impact.tests, roots)
        if context.impact is not None
        else None
    )
    result_tuple = tuple(retrieval_results)
    redaction_reasons = tuple(query_document.redaction.reasons)
    if protected_items_omitted:
        redaction_reasons = tuple(
            sorted({*redaction_reasons, "protected_repository_items"})
        )
    return RepositoryIntelligenceTraceEvidence(
        search_query=safe_query,
        search_query_sha256=_document_hash(safe_query),
        context_hash=context.context_hash,
        snapshot=snapshot,
        retrieval_results=result_tuple,
        retrieval_result_hashes=tuple(item.result_hash for item in result_tuple),
        retrieval_scoring_sha256=_retrieval_scoring_hash(result_tuple),
        retrieval_truncated=retrieval_truncated,
        context_plan_id=(
            context.context_plan.plan_id if context.context_plan is not None else None
        ),
        context_plan_hash=(
            context.context_plan.plan_hash
            if context.context_plan is not None
            else None
        ),
        dependency_result_hash=_document_hash(safe_dependencies),
        architecture_result_hash=_document_hash(
            [item.model_dump(mode="json") for item in boundaries]
        ),
        architecture_boundaries=tuple(boundaries),
        impact_result=impact,
        test_selection_result=tests,
        protected_items_omitted=protected_items_omitted,
        redaction_reasons=redaction_reasons,
    )


def reuse_captured_repository_intelligence(
    captured: RepositoryIntelligenceTraceEvidence,
) -> RepositoryIntelligenceReplayReport:
    return RepositoryIntelligenceReplayReport(
        captured_context_hash=captured.context_hash,
        captured_snapshot_id=(
            captured.snapshot.snapshot_id if captured.snapshot is not None else None
        ),
        captured_evidence_reused=True,
        captured_snapshot_reused=captured.snapshot is not None,
    )


def unavailable_current_repository_intelligence(
    captured: RepositoryIntelligenceTraceEvidence,
) -> RepositoryIntelligenceReplayReport:
    return RepositoryIntelligenceReplayReport(
        captured_context_hash=captured.context_hash,
        captured_snapshot_id=(
            captured.snapshot.snapshot_id if captured.snapshot is not None else None
        ),
        captured_evidence_reused=True,
        captured_snapshot_reused=captured.snapshot is not None,
        compared_current=True,
        index_drift=True,
        findings=(
            IntelligenceDriftFinding(
                category=IntelligenceDriftCategory.CURRENT_INDEX_UNAVAILABLE,
                historical_sha256=captured.context_hash,
                summary=(
                    "Captured repository intelligence was reused because the current "
                    "local index was unavailable."
                ),
            ),
        ),
    )


def compare_repository_intelligence(
    captured: RepositoryIntelligenceTraceEvidence,
    current: RepositoryIntelligenceTraceEvidence,
) -> RepositoryIntelligenceReplayReport:
    findings: list[IntelligenceDriftFinding] = []

    def compare(
        category: IntelligenceDriftCategory,
        historical: Any,
        present: Any,
        summary: str,
    ) -> None:
        if historical == present:
            return
        findings.append(
            IntelligenceDriftFinding(
                category=category,
                historical_sha256=_optional_document_hash(historical),
                current_sha256=_optional_document_hash(present),
                summary=summary,
            )
        )

    historical_snapshot = captured.snapshot
    current_snapshot = current.snapshot
    compare(
        IntelligenceDriftCategory.INDEX_SNAPSHOT,
        (
            historical_snapshot.snapshot_id,
            historical_snapshot.source_fingerprint,
            historical_snapshot.state,
        )
        if historical_snapshot
        else None,
        (
            current_snapshot.snapshot_id,
            current_snapshot.source_fingerprint,
            current_snapshot.state,
        )
        if current_snapshot
        else None,
        "Repository index snapshot identity or source fingerprint changed.",
    )
    compare(
        IntelligenceDriftCategory.PARSER_VERSIONS,
        historical_snapshot.parser_versions if historical_snapshot else None,
        current_snapshot.parser_versions if current_snapshot else None,
        "Repository parser versions changed.",
    )
    compare(
        IntelligenceDriftCategory.PROJECT_MAP,
        historical_snapshot.project_map_hash if historical_snapshot else None,
        current_snapshot.project_map_hash if current_snapshot else None,
        "Repository project-map evidence changed.",
    )
    compare(
        IntelligenceDriftCategory.GRAPH,
        historical_snapshot.graph_hash if historical_snapshot else None,
        current_snapshot.graph_hash if current_snapshot else None,
        "Repository dependency graph changed.",
    )
    compare(
        IntelligenceDriftCategory.GRAPH_RESULTS,
        captured.dependency_result_hash,
        current.dependency_result_hash,
        "Historical and current dependency-query results differ.",
    )
    compare(
        IntelligenceDriftCategory.ARCHITECTURE,
        captured.architecture_result_hash,
        current.architecture_result_hash,
        "Architecture boundary evidence changed.",
    )
    compare(
        IntelligenceDriftCategory.RETRIEVAL_RESULTS,
        captured.retrieval_result_hashes,
        current.retrieval_result_hashes,
        "Repository retrieval results changed.",
    )
    compare(
        IntelligenceDriftCategory.RETRIEVAL_SCORING,
        captured.retrieval_scoring_sha256,
        current.retrieval_scoring_sha256,
        "Repository retrieval scoring explanations changed.",
    )
    compare(
        IntelligenceDriftCategory.CONTEXT_PLAN,
        captured.context_plan_hash,
        current.context_plan_hash,
        "Selected repository context changed.",
    )
    compare(
        IntelligenceDriftCategory.IMPACT,
        (
            captured.impact_result.result_hash
            if captured.impact_result is not None
            else None
        ),
        current.impact_result.result_hash if current.impact_result is not None else None,
        "Repository impact analysis changed.",
    )
    compare(
        IntelligenceDriftCategory.TEST_SELECTION,
        (
            captured.test_selection_result.result_hash
            if captured.test_selection_result is not None
            else None
        ),
        (
            current.test_selection_result.result_hash
            if current.test_selection_result is not None
            else None
        ),
        "Repository test selection changed.",
    )
    categories = {item.category for item in findings}
    index_categories = {
        IntelligenceDriftCategory.INDEX_SNAPSHOT,
        IntelligenceDriftCategory.PARSER_VERSIONS,
        IntelligenceDriftCategory.PROJECT_MAP,
        IntelligenceDriftCategory.GRAPH,
        IntelligenceDriftCategory.GRAPH_RESULTS,
        IntelligenceDriftCategory.ARCHITECTURE,
    }
    retrieval_categories = {
        IntelligenceDriftCategory.RETRIEVAL_RESULTS,
        IntelligenceDriftCategory.RETRIEVAL_SCORING,
        IntelligenceDriftCategory.CONTEXT_PLAN,
    }
    return RepositoryIntelligenceReplayReport(
        captured_context_hash=captured.context_hash,
        current_context_hash=current.context_hash,
        captured_snapshot_id=(
            captured.snapshot.snapshot_id if captured.snapshot is not None else None
        ),
        current_snapshot_id=(
            current.snapshot.snapshot_id if current.snapshot is not None else None
        ),
        captured_evidence_reused=True,
        captured_snapshot_reused=captured.snapshot is not None,
        compared_current=True,
        index_drift=bool(categories.intersection(index_categories)),
        retrieval_drift=bool(categories.intersection(retrieval_categories)),
        findings=tuple(findings),
    )


def _boundary_evidence(
    boundary: ArchitectureBoundary,
    *,
    protected_paths: set[str],
    private_roots: tuple[str | Path, ...],
) -> ArchitectureBoundaryTraceEvidence | None:
    scope = tuple(
        value
        for value in _safe_values(
            boundary.scope,
            private_roots,
            maximum=256,
            maximum_characters=2_000,
        )
        if value.casefold() not in protected_paths
    )
    if not scope:
        return None
    forbidden_targets = tuple(
        value
        for value in _safe_values(
            boundary.forbidden_targets,
            private_roots,
            maximum=256,
            maximum_characters=2_000,
        )
        if value.casefold() not in protected_paths
    )
    payload = {
        "boundary_id": _safe_text(boundary.boundary_id, private_roots, 256),
        "boundary_type": boundary.boundary_type,
        "scope": scope,
        "forbidden_targets": forbidden_targets,
        "confidence": boundary.confidence,
    }
    return ArchitectureBoundaryTraceEvidence(
        **payload,
        boundary_hash=_document_hash(payload),
    )


def _impact_evidence(
    impact: ImpactResult,
    private_roots: tuple[str | Path, ...],
) -> ImpactTraceEvidence:
    transitive = _safe_values(
        impact.transitive_dependents,
        private_roots,
        maximum=_MAX_IMPACT_NODES,
        maximum_characters=2_000,
    )
    direct = _safe_values(
        impact.direct_dependents,
        private_roots,
        maximum=_MAX_IMPACT_NODES,
        maximum_characters=2_000,
    )
    return ImpactTraceEvidence(
        result_id=_safe_text(impact.result_id, private_roots, 256),
        snapshot_id=(
            _safe_text(impact.snapshot_id, private_roots, 256)
            if impact.snapshot_id
            else None
        ),
        result_hash=_document_hash(
            impact.model_dump(mode="json"),
            private_roots=private_roots,
        ),
        risk=impact.risk.value,
        confidence=impact.confidence,
        changed_paths=_safe_values(
            impact.changed_paths,
            private_roots,
            maximum=1_000,
            maximum_characters=2_000,
        ),
        changed_symbols=_safe_values(
            impact.changed_symbols,
            private_roots,
            maximum=1_000,
            maximum_characters=2_000,
        ),
        direct_dependents=direct,
        transitive_dependents=transitive,
        affected_projects=_safe_values(
            impact.affected_projects,
            private_roots,
            maximum=1_000,
            maximum_characters=256,
        ),
        affected_public_apis=_safe_values(
            impact.affected_public_apis,
            private_roots,
            maximum=2_000,
            maximum_characters=2_000,
        ),
        affected_endpoints=_safe_values(
            impact.affected_endpoints,
            private_roots,
            maximum=2_000,
            maximum_characters=2_000,
        ),
        affected_configurations=_safe_values(
            impact.affected_configurations,
            private_roots,
            maximum=2_000,
            maximum_characters=2_000,
        ),
        architecture_crossings=_safe_values(
            impact.architecture_crossings,
            private_roots,
            maximum=2_000,
            maximum_characters=2_000,
        ),
        ownership_rules=_safe_values(
            impact.ownership_rules,
            private_roots,
            maximum=1_000,
            maximum_characters=2_000,
        ),
        integration_hotspots=_safe_values(
            impact.integration_hotspots,
            private_roots,
            maximum=2_000,
            maximum_characters=2_000,
        ),
        uncertainty=_safe_values(
            impact.uncertainty,
            private_roots,
            maximum=256,
            maximum_characters=1_000,
        ),
        evidence_hashes=tuple(
            sorted(
                {
                    _document_hash(
                        _safe_text(value, private_roots, 2_000)
                    )
                    for value in impact.evidence[:_MAX_IMPACT_EVIDENCE]
                }
            )
        ),
        source_truncated=(
            impact.truncated
            or len(impact.direct_dependents) > len(direct)
            or len(impact.transitive_dependents) > len(transitive)
            or len(impact.evidence) > _MAX_IMPACT_EVIDENCE
        ),
    )


def _test_selection_evidence(
    tests: TestImpactResult,
    private_roots: tuple[str | Path, ...],
) -> TestSelectionTraceEvidence:
    return TestSelectionTraceEvidence(
        result_id=_safe_text(tests.result_id, private_roots, 256),
        result_hash=_document_hash(
            tests.model_dump(mode="json"),
            private_roots=private_roots,
        ),
        selected_tests=_safe_values(
            tests.selected_tests,
            private_roots,
            maximum=2_000,
            maximum_characters=2_000,
        ),
        mandatory_tests=_safe_values(
            tests.mandatory_tests,
            private_roots,
            maximum=2_000,
            maximum_characters=2_000,
        ),
        optional_tests=_safe_values(
            tests.optional_tests,
            private_roots,
            maximum=2_000,
            maximum_characters=2_000,
        ),
        full_suite_recommended=tests.full_suite_recommended,
        confidence=tests.confidence,
        evidence_hashes=tuple(
            sorted(
                {
                    _document_hash(
                        _safe_text(value, private_roots, 2_000)
                    )
                    for value in tests.evidence[:2_000]
                }
            )
        ),
        escalation_reasons=_safe_values(
            tests.escalation_reasons,
            private_roots,
            maximum=256,
            maximum_characters=1_000,
        ),
    )


def _retrieval_scoring_hash(
    results: Iterable[RetrievalTraceEvidence],
) -> str:
    return _document_hash(
        [
            {
                "result_hash": item.result_hash,
                "score": item.score,
                "scoring_explanations": item.scoring_explanations,
                "selected": item.selected,
            }
            for item in results
        ]
    )


def _safe_values(
    values: Iterable[str],
    private_roots: tuple[str | Path, ...],
    *,
    maximum: int,
    maximum_characters: int,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                _safe_text(value, private_roots, maximum_characters)
                for value in values
                if value
            }
        )[:maximum]
    )


def _safe_text(
    value: str,
    private_roots: tuple[str | Path, ...],
    maximum_characters: int,
) -> str:
    document = sanitize_text(
        str(value),
        private_roots=private_roots,
        max_chars=max(1, maximum_characters - 16),
    )
    return str(document.value)[:maximum_characters] or "[REDACTED]"


def _optional_document_hash(value: Any) -> str | None:
    if value is None:
        return None
    if (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        return value
    return _document_hash(value)


def _document_hash(
    value: Any,
    *,
    private_roots: Iterable[str | Path] = (),
) -> str:
    safe = sanitize_document(value, private_roots=private_roots)
    return hashlib.sha256(canonical_json_bytes(safe.value)).hexdigest()


__all__ = [
    "ArchitectureBoundaryTraceEvidence",
    "ImpactTraceEvidence",
    "IndexSnapshotTraceEvidence",
    "IntelligenceDriftCategory",
    "IntelligenceDriftFinding",
    "REPOSITORY_INTELLIGENCE_COMPONENT",
    "REPOSITORY_INTELLIGENCE_EVIDENCE_MEDIA_TYPE",
    "REPOSITORY_INTELLIGENCE_EVIDENCE_SCHEMA_VERSION",
    "RepositoryIntelligenceReplayReport",
    "RepositoryIntelligenceTraceEvidence",
    "RetrievalTraceEvidence",
    "TestSelectionTraceEvidence",
    "build_repository_intelligence_trace_evidence",
    "compare_repository_intelligence",
    "reuse_captured_repository_intelligence",
    "unavailable_current_repository_intelligence",
]
