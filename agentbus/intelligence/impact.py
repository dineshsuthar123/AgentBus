from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from agentbus.intelligence.architecture import ArchitectureInference
from agentbus.intelligence.discovery.ignore import glob_match
from agentbus.intelligence.errors import QueryLimitError
from agentbus.intelligence.identities import (
    impact_result_id,
    stable_hash,
)
from agentbus.intelligence.models import (
    ArchitectureBoundary,
    DependencyEdge,
    ImpactRequest,
    ImpactResult,
    Module,
    OwnershipRule,
    Project,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
)
from agentbus.intelligence.ownership import OwnershipExtraction
from agentbus.intelligence.risk import (
    EvidenceBackedRiskAssessor,
    RiskSignals,
)
from agentbus.intelligence.test_impact import (
    HistoricalTestFixture,
    TestImpactSelector,
)
from agentbus.intelligence.traversal import (
    DependencyGraph,
    TraversalLimits,
)


_OWNERSHIP_PATHS = {
    ".github/codeowners",
    "codeowners",
    "docs/codeowners",
}
_CONFIGURATION_NAMES = {
    ".editorconfig",
    "application.properties",
    "application.yml",
    "application.yaml",
    "build.gradle",
    "build.gradle.kts",
    "go.mod",
    "go.work",
    "gradle.properties",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "settings.gradle",
    "settings.gradle.kts",
    "tsconfig.json",
}
_CONFIGURATION_LANGUAGES = {
    SourceLanguage.JSON,
    SourceLanguage.TOML,
    SourceLanguage.YAML,
}


@dataclass(frozen=True)
class ImpactAnalysisLimits:
    maximum_proposed_edges: int = 10_000
    maximum_graph_edges: int = 100_000
    maximum_hotspots: int = 2_000
    maximum_cycle_evidence: int = 256
    maximum_evidence: int = 5_000
    maximum_uncertainty: int = 256

    def __post_init__(self) -> None:
        _bounded(
            self.maximum_proposed_edges,
            "maximum_proposed_edges",
            1,
            100_000,
        )
        _bounded(
            self.maximum_graph_edges,
            "maximum_graph_edges",
            1,
            1_000_000,
        )
        _bounded(self.maximum_hotspots, "maximum_hotspots", 1, 2_000)
        _bounded(
            self.maximum_cycle_evidence,
            "maximum_cycle_evidence",
            1,
            1_000,
        )
        _bounded(self.maximum_evidence, "maximum_evidence", 1, 5_000)
        _bounded(
            self.maximum_uncertainty,
            "maximum_uncertainty",
            1,
            256,
        )


class ChangeImpactAnalyzer:
    """Compute bounded, evidence-attributed impact over a dependency graph."""

    def __init__(
        self,
        graph: DependencyGraph,
        *,
        projects: Iterable[Project] = (),
        files: Iterable[SourceFile] | None = None,
        symbols: Iterable[Symbol] | None = None,
        architecture: ArchitectureInference | None = None,
        boundaries: Iterable[ArchitectureBoundary] = (),
        ownership: OwnershipExtraction | Iterable[OwnershipRule] | None = None,
        risk_assessor: EvidenceBackedRiskAssessor | None = None,
        test_selector: TestImpactSelector | None = None,
        limits: ImpactAnalysisLimits | None = None,
    ) -> None:
        self.limits = limits or ImpactAnalysisLimits()
        all_files = tuple(graph.files if files is None else files)
        all_symbols = tuple(graph.symbols if symbols is None else symbols)
        protected_file_ids = {
            item.file_id for item in all_files if item.protected
        }
        protected_symbol_ids = {
            item.symbol_id
            for item in all_symbols
            if item.file_id in protected_file_ids
        }
        protected_paths = {
            item.relative_path for item in all_files if item.protected
        }
        protected_module_ids = {
            item.module_id
            for item in graph.modules
            if item.relative_path in protected_paths
        }
        self._protected_file_ids = protected_file_ids
        self._protected_node_ids = (
            protected_file_ids | protected_symbol_ids | protected_module_ids
        )
        self.files = tuple(
            sorted(
                (
                    item
                    for item in all_files
                    if item.file_id not in protected_file_ids
                ),
                key=lambda item: item.file_id,
            )
        )
        self.symbols = tuple(
            sorted(
                (
                    item
                    for item in all_symbols
                    if item.file_id not in protected_file_ids
                ),
                key=lambda item: item.symbol_id,
            )
        )
        self.projects = tuple(
            sorted(projects, key=lambda item: item.project_id)
        )
        self.modules = tuple(
            item
            for item in graph.modules
            if item.module_id not in protected_module_ids
        )
        safe_edges = tuple(
            edge
            for edge in graph.edges
            if (
                edge.source_id not in protected_file_ids
                and edge.target_id not in protected_file_ids
                and edge.source_id not in protected_symbol_ids
                and edge.target_id not in protected_symbol_ids
                and edge.source_id not in protected_module_ids
                and edge.target_id not in protected_module_ids
            )
        )
        self.graph = DependencyGraph(
            safe_edges,
            files=self.files,
            symbols=self.symbols,
            modules=self.modules,
            limits=graph.limits,
        )
        self.architecture = architecture
        boundary_records = {
            item.boundary_id: item
            for item in (
                *(architecture.boundaries if architecture else ()),
                *tuple(boundaries),
            )
        }
        self.boundaries = tuple(
            sorted(boundary_records.values(), key=lambda item: item.boundary_id)
        )
        self.ownership = _ownership(ownership)
        self.ownership_available = ownership is not None
        self.risk_assessor = risk_assessor or EvidenceBackedRiskAssessor()
        self.test_selector = test_selector
        self._all_files_by_path = {
            item.relative_path: item for item in all_files
        }
        self._files_by_path = {
            item.relative_path: item for item in self.files
        }
        self._files_by_id = {item.file_id: item for item in self.files}
        self._all_symbols_by_id = {
            item.symbol_id: item for item in all_symbols
        }
        self._symbols_by_id = {
            item.symbol_id: item for item in self.symbols
        }
        self._symbols_by_file: dict[str, list[Symbol]] = {}
        for symbol in self.symbols:
            self._symbols_by_file.setdefault(symbol.file_id, []).append(symbol)
        self._node_projects = _node_projects(
            self.files,
            self.symbols,
            self.modules,
        )
        self._node_paths = _node_paths(
            self.files,
            self.symbols,
            self.modules,
        )
        self._manifest_paths = {
            path for project in self.projects for path in project.manifest_paths
        }

    def analyze(
        self,
        request: ImpactRequest,
        *,
        snapshot_id: str | None = None,
        proposed_edges: Iterable[DependencyEdge] = (),
        previous_ownership_rules: Iterable[OwnershipRule] | None = None,
        mandatory_tests: Iterable[str] = (),
        historical_test_fixtures: Iterable[HistoricalTestFixture] = (),
    ) -> ImpactResult:
        proposed_records = tuple(
            sorted(proposed_edges, key=lambda item: item.edge_id)
        )
        if len(proposed_records) > self.limits.maximum_proposed_edges:
            raise QueryLimitError(
                "Proposed dependency edges exceed the impact-analysis limit."
            )

        uncertainty: list[str] = []
        evidence: list[str] = []
        confidences: list[float] = []
        truncated = False
        changed_paths: set[str] = set()
        known_changed_symbols: set[str] = set()
        reported_changed_symbols: set[str] = set()
        start_ids: set[str] = set()
        proposed = tuple(
            edge
            for edge in proposed_records
            if (
                edge.source_id not in self._protected_node_ids
                and edge.target_id not in self._protected_node_ids
            )
        )
        if len(proposed) != len(proposed_records):
            uncertainty.append("protected_proposed_dependency_omitted")
        known_nodes = set(self.graph.node_ids)
        if any(
            edge.source_id not in known_nodes or edge.target_id not in known_nodes
            for edge in proposed
        ):
            uncertainty.append("proposed_dependency_node_not_indexed")

        for path in request.paths:
            source = self._all_files_by_path.get(path)
            if source is not None and source.protected:
                uncertainty.append("protected_changed_path_omitted")
                continue
            changed_paths.add(path)
            if source is None:
                uncertainty.append(f"changed_path_not_indexed:{path}")
                continue
            start_ids.add(source.file_id)
            confidences.append(1.0)
            evidence.append(
                f"subject.path:{source.relative_path}:{source.content_hash}"
            )
            for symbol in self._symbols_by_file.get(source.file_id, ()):
                known_changed_symbols.add(symbol.symbol_id)
                reported_changed_symbols.add(symbol.symbol_id)
                start_ids.add(symbol.symbol_id)

        for symbol_id in request.symbol_ids:
            original = self._all_symbols_by_id.get(symbol_id)
            if (
                original is not None
                and original.file_id in self._protected_file_ids
            ):
                uncertainty.append("protected_changed_symbol_omitted")
                continue
            reported_changed_symbols.add(symbol_id)
            symbol = self._symbols_by_id.get(symbol_id)
            if symbol is None:
                uncertainty.append(f"changed_symbol_not_indexed:{symbol_id}")
                continue
            known_changed_symbols.add(symbol_id)
            start_ids.add(symbol_id)

        for symbol_id in sorted(known_changed_symbols):
            symbol = self._symbols_by_id[symbol_id]
            confidences.append(symbol.confidence)
            evidence.append(
                f"subject.symbol:{symbol.symbol_id}:{symbol.confidence:.6f}"
            )

        projected_edges = _merge_edges(self.graph.edges, proposed)
        working_graph = DependencyGraph(
            projected_edges,
            files=self.files,
            symbols=self.symbols,
            modules=self.modules,
            limits=TraversalLimits(
                maximum_depth=request.max_depth,
                maximum_nodes=request.max_nodes,
                maximum_edges=self.limits.maximum_graph_edges,
            ),
        )
        traversal_starts = tuple(sorted(start_ids))
        if len(traversal_starts) > request.max_nodes:
            traversal_starts = traversal_starts[: request.max_nodes]
            uncertainty.append("impact_start_nodes_truncated")
            truncated = True

        direct_nodes: set[str] = set()
        impact_edges: dict[str, DependencyEdge] = {}
        for node_id in traversal_starts:
            try:
                adjacent = working_graph.dependents(node_id)
            except QueryLimitError:
                uncertainty.append("direct_dependency_query_truncated")
                truncated = True
                break
            for edge in adjacent:
                impact_edges[edge.edge_id] = edge
                if edge.source_id not in start_ids:
                    direct_nodes.add(edge.source_id)
        if len(direct_nodes) > request.max_nodes:
            direct_nodes = set(sorted(direct_nodes)[: request.max_nodes])
            uncertainty.append("direct_dependents_truncated")
            truncated = True

        transitive_nodes: set[str] = set()
        if traversal_starts and request.max_depth > 0:
            traversal = working_graph.transitive_dependents(
                traversal_starts,
                max_depth=request.max_depth,
            )
            transitive_nodes.update(traversal.node_ids)
            impact_edges.update(
                (edge.edge_id, edge) for edge in traversal.edges
            )
            if traversal.truncated:
                uncertainty.append("transitive_dependency_query_truncated")
                truncated = True

        for edge in proposed:
            if edge.source_id in start_ids or edge.target_id in start_ids:
                impact_edges[edge.edge_id] = edge
        affected_nodes = start_ids | direct_nodes | transitive_nodes
        for edge in sorted(impact_edges.values(), key=lambda item: item.edge_id):
            confidences.append(edge.confidence)
            evidence.append(
                "dependency.edge:"
                f"{edge.edge_id}:{edge.kind.value}:{edge.confidence:.6f}"
            )

        affected_projects = {
            project_id
            for node_id in affected_nodes
            if (project_id := self._node_projects.get(node_id)) is not None
        }
        affected_symbols = tuple(
            self._symbols_by_id[node_id]
            for node_id in sorted(affected_nodes)
            if node_id in self._symbols_by_id
        )
        affected_public_apis = {
            item.symbol_id for item in affected_symbols if item.exported
        }
        affected_endpoints = {
            item.symbol_id
            for item in affected_symbols
            if item.endpoint is not None or item.kind == SymbolKind.ENDPOINT
        }
        affected_configurations = {
            item.symbol_id
            for item in affected_symbols
            if item.kind == SymbolKind.CONFIGURATION_UNIT
        }
        affected_configurations.update(
            source.file_id
            for source in self.files
            if (
                source.file_id in affected_nodes
                and self._is_configuration(source)
            )
        )

        crossing_ids, forbidden_ids, boundary_confidences = (
            self._architecture_effects(
                tuple(impact_edges.values()),
                changed_paths,
                evidence,
            )
        )
        confidences.extend(boundary_confidences)
        if not self.boundaries and changed_paths:
            uncertainty.append("architecture_evidence_unavailable")

        introduced_cycles = self._introduced_cycles(
            proposed,
            projected_edges,
            evidence,
            uncertainty,
        )
        if introduced_cycles is None:
            truncated = True
            introduced_cycle_count = 0
        else:
            introduced_cycle_count = len(introduced_cycles)

        ownership_rules, ownership_changed = self._ownership_effects(
            changed_paths,
            previous_ownership_rules,
            evidence,
            confidences,
        )
        if not self.ownership_available and changed_paths:
            uncertainty.append("ownership_evidence_unavailable")

        hotspots = self._integration_hotspots(
            working_graph,
            affected_nodes,
            tuple(impact_edges.values()),
            evidence,
            uncertainty,
        )
        security_sensitive = any(
            boundary.boundary_type == "security_sensitive"
            for path in changed_paths
            for boundary in self._boundaries_for(path)
        )
        unique_uncertainty = tuple(
            dict.fromkeys(uncertainty)
        )[: self.limits.maximum_uncertainty]
        if any(item.endswith("_truncated") for item in unique_uncertainty):
            truncated = True
        if len(tuple(dict.fromkeys(uncertainty))) > len(unique_uncertainty):
            truncated = True

        assessment = self.risk_assessor.assess(
            RiskSignals(
                affected_public_apis=len(affected_public_apis),
                affected_endpoints=len(affected_endpoints),
                affected_configurations=len(affected_configurations),
                affected_projects=len(affected_projects),
                architecture_crossings=len(crossing_ids),
                forbidden_crossings=len(forbidden_ids),
                introduced_cycles=introduced_cycle_count,
                integration_hotspots=len(hotspots),
                security_sensitive_change=security_sensitive,
                ownership_metadata_changed=ownership_changed,
            ),
            evidence_confidences=tuple(confidences),
            uncertainty=unique_uncertainty,
        )
        evidence.extend(assessment.evidence)

        test_selector = self.test_selector or TestImpactSelector(
            working_graph,
            projects=self.projects,
            files=self.files,
            symbols=self.symbols,
        )
        tests = test_selector.select(
            request,
            snapshot_id=snapshot_id,
            affected_node_ids=affected_nodes,
            direct_dependent_ids=direct_nodes,
            affected_project_ids=affected_projects,
            affected_configuration_ids=affected_configurations,
            configured_mandatory_tests=mandatory_tests,
            historical_fixtures=historical_test_fixtures,
            subject_paths=tuple(sorted(changed_paths)),
            subject_symbol_ids=tuple(sorted(reported_changed_symbols)),
            risk=assessment.risk,
            impact_confidence=assessment.confidence,
            impact_truncated=truncated,
        )
        if any(
            "truncated" in reason or "limit_reached" in reason
            for reason in tests.escalation_reasons
        ):
            truncated = True

        result_values = (
            ("changed_symbols", reported_changed_symbols, 1_000),
            ("direct_dependents", direct_nodes, 5_000),
            ("transitive_dependents", transitive_nodes, 10_000),
            ("affected_projects", affected_projects, 1_000),
            ("affected_public_apis", affected_public_apis, 2_000),
            ("affected_endpoints", affected_endpoints, 2_000),
            ("affected_configurations", affected_configurations, 2_000),
            ("architecture_crossings", crossing_ids, 2_000),
            ("ownership_rules", ownership_rules, 1_000),
            ("integration_hotspots", hotspots, 2_000),
        )
        bounded: dict[str, tuple[str, ...]] = {}
        for name, values, maximum in result_values:
            bounded[name], was_truncated = _take(values, maximum)
            truncated = truncated or was_truncated
        bounded_evidence, evidence_truncated = _take(
            evidence,
            self.limits.maximum_evidence,
        )
        truncated = truncated or evidence_truncated
        if truncated and not tests.full_suite_recommended:
            tests = tests.model_copy(
                update={
                    "full_suite_recommended": True,
                    "escalation_reasons": tuple(
                        dict.fromkeys(
                            (*tests.escalation_reasons, "analysis_truncated")
                        )
                    ),
                }
            )
        return ImpactResult(
            result_id=impact_result_id(
                snapshot_id,
                tuple(changed_paths),
                tuple(reported_changed_symbols),
            ),
            snapshot_id=snapshot_id,
            changed_paths=tuple(sorted(changed_paths)),
            changed_symbols=bounded["changed_symbols"],
            direct_dependents=bounded["direct_dependents"],
            transitive_dependents=bounded["transitive_dependents"],
            affected_projects=bounded["affected_projects"],
            affected_public_apis=bounded["affected_public_apis"],
            affected_endpoints=bounded["affected_endpoints"],
            affected_configurations=bounded["affected_configurations"],
            architecture_crossings=bounded["architecture_crossings"],
            ownership_rules=bounded["ownership_rules"],
            integration_hotspots=bounded["integration_hotspots"],
            risk=assessment.risk,
            confidence=assessment.confidence,
            uncertainty=assessment.uncertainty,
            evidence=bounded_evidence,
            tests=tests,
            truncated=truncated,
        )

    def _is_configuration(self, source: SourceFile) -> bool:
        return (
            source.language in _CONFIGURATION_LANGUAGES
            or source.relative_path in self._manifest_paths
            or PurePosixPath(source.relative_path).name.casefold()
            in _CONFIGURATION_NAMES
        )

    def _architecture_effects(
        self,
        edges: tuple[DependencyEdge, ...],
        changed_paths: set[str],
        evidence: list[str],
    ) -> tuple[set[str], set[str], list[float]]:
        crossings: set[str] = set()
        forbidden: set[str] = set()
        confidences: list[float] = []
        for path in sorted(changed_paths):
            for boundary in self._boundaries_for(path):
                evidence.append(
                    "architecture.boundary:"
                    f"{boundary.boundary_id}:{boundary.boundary_type}:"
                    f"{boundary.confidence:.6f}"
                )
                confidences.append(boundary.confidence)
        for edge in sorted(edges, key=lambda item: item.edge_id):
            source_path = self._node_paths.get(edge.source_id)
            target_path = self._node_paths.get(edge.target_id)
            if source_path is None or target_path is None:
                continue
            source_boundaries = self._boundaries_for(source_path)
            target_boundaries = self._boundaries_for(target_path)
            source_ids = {item.boundary_id for item in source_boundaries}
            target_ids = {item.boundary_id for item in target_boundaries}
            project_crossing = (
                self._node_projects.get(edge.source_id) is not None
                and self._node_projects.get(edge.target_id) is not None
                and self._node_projects[edge.source_id]
                != self._node_projects[edge.target_id]
            )
            if project_crossing or source_ids != target_ids:
                crossings.add(edge.edge_id)
                evidence.append(f"architecture.crossing:{edge.edge_id}")
            for boundary in source_boundaries:
                if boundary.boundary_type != "forbidden_dependency":
                    continue
                if any(
                    glob_match(target_path, pattern)
                    for pattern in boundary.forbidden_targets
                ):
                    forbidden.add(edge.edge_id)
                    evidence.append(
                        "architecture.forbidden_crossing:"
                        f"{boundary.boundary_id}:{edge.edge_id}"
                    )
                    confidences.append(boundary.confidence)
        return crossings, forbidden, confidences

    def _introduced_cycles(
        self,
        proposed: tuple[DependencyEdge, ...],
        projected_edges: tuple[DependencyEdge, ...],
        evidence: list[str],
        uncertainty: list[str],
    ) -> tuple[tuple[str, ...], ...] | None:
        if not proposed:
            return ()
        try:
            cycle_limits = TraversalLimits(
                maximum_depth=self.graph.limits.maximum_depth,
                maximum_nodes=self.graph.limits.maximum_nodes,
                maximum_edges=min(
                    self.graph.limits.maximum_edges,
                    self.limits.maximum_graph_edges,
                ),
            )
            baseline_graph = DependencyGraph(
                self.graph.edges,
                files=self.files,
                symbols=self.symbols,
                modules=self.modules,
                limits=cycle_limits,
            )
            baseline = {item.node_ids for item in baseline_graph.cycles()}
            projected_graph = DependencyGraph(
                projected_edges,
                files=self.files,
                symbols=self.symbols,
                modules=self.modules,
                limits=cycle_limits,
            )
            introduced = tuple(
                item.node_ids
                for item in projected_graph.cycles()
                if item.node_ids not in baseline
            )
        except QueryLimitError:
            uncertainty.append("introduced_cycle_analysis_truncated")
            return None
        for nodes in introduced[: self.limits.maximum_cycle_evidence]:
            display = ",".join(nodes[:20])
            if len(nodes) > 20:
                display += f",...({len(nodes)} nodes)"
            evidence.append(f"dependency.introduced_cycle:{display}")
        if len(introduced) > self.limits.maximum_cycle_evidence:
            uncertainty.append("introduced_cycle_evidence_truncated")
        return introduced

    def _ownership_effects(
        self,
        changed_paths: set[str],
        previous_rules: Iterable[OwnershipRule] | None,
        evidence: list[str],
        confidences: list[float],
    ) -> tuple[set[str], bool]:
        rule_ids: set[str] = set()
        for path in sorted(changed_paths):
            effective = self.ownership.effective_rule_for(path)
            if effective is None:
                continue
            rule_ids.add(effective.rule_id)
            confidences.append(effective.confidence)
            evidence.append(
                "ownership.rule:"
                f"{effective.rule_id}:{effective.confidence:.6f}"
            )
        metadata_changed = any(
            path.casefold() in _OWNERSHIP_PATHS for path in changed_paths
        )
        if previous_rules is not None:
            before_hash = _ownership_hash(previous_rules)
            after_hash = _ownership_hash(self.ownership.rules)
            metadata_changed = metadata_changed or before_hash != after_hash
        if metadata_changed:
            rule_ids.update(rule.rule_id for rule in self.ownership.rules)
            evidence.append("ownership.metadata_changed")
        return rule_ids, metadata_changed

    def _integration_hotspots(
        self,
        graph: DependencyGraph,
        affected_nodes: set[str],
        edges: tuple[DependencyEdge, ...],
        evidence: list[str],
        uncertainty: list[str],
    ) -> set[str]:
        hotspots: set[str] = set()
        try:
            centrality = graph.high_centrality(
                limit=min(self.limits.maximum_hotspots, 1_000)
            )
        except QueryLimitError:
            uncertainty.append("integration_hotspot_analysis_truncated")
            centrality = ()
        for score in centrality:
            if (
                score.node_id in affected_nodes
                and score.inbound_edges + score.outbound_edges >= 3
            ):
                hotspots.add(score.node_id)
                evidence.append(
                    "integration.hotspot:"
                    f"{score.node_id}:{score.inbound_edges}:"
                    f"{score.outbound_edges}"
                )
        for edge in edges:
            source_project = self._node_projects.get(edge.source_id)
            target_project = self._node_projects.get(edge.target_id)
            if (
                source_project is not None
                and target_project is not None
                and source_project != target_project
            ):
                hotspots.update((edge.source_id, edge.target_id))
        return set(sorted(hotspots)[: self.limits.maximum_hotspots])

    def _boundaries_for(self, path: str) -> tuple[ArchitectureBoundary, ...]:
        return tuple(
            boundary
            for boundary in self.boundaries
            if any(glob_match(path, scope) for scope in boundary.scope)
        )


ImpactAnalyzer = ChangeImpactAnalyzer


def _ownership(
    value: OwnershipExtraction | Iterable[OwnershipRule] | None,
) -> OwnershipExtraction:
    if isinstance(value, OwnershipExtraction):
        return value
    return OwnershipExtraction(
        rules=tuple(value or ()),
        diagnostics=(),
    )


def _ownership_hash(rules: Iterable[OwnershipRule]) -> str:
    return stable_hash(
        tuple(
            rule.model_dump(mode="json")
            for rule in sorted(rules, key=lambda item: item.rule_id)
        )
    )


def _merge_edges(
    current: tuple[DependencyEdge, ...],
    proposed: tuple[DependencyEdge, ...],
) -> tuple[DependencyEdge, ...]:
    by_id = {edge.edge_id: edge for edge in current}
    by_id.update((edge.edge_id, edge) for edge in proposed)
    return tuple(sorted(by_id.values(), key=lambda item: item.edge_id))


def _node_projects(
    files: tuple[SourceFile, ...],
    symbols: tuple[Symbol, ...],
    modules: tuple[Module, ...],
) -> dict[str, str]:
    values = {
        item.file_id: item.project_id
        for item in files
        if item.project_id is not None
    }
    values.update(
        {
            item.symbol_id: item.project_id
            for item in symbols
            if item.project_id is not None
        }
    )
    values.update({item.module_id: item.project_id for item in modules})
    return values


def _node_paths(
    files: tuple[SourceFile, ...],
    symbols: tuple[Symbol, ...],
    modules: tuple[Module, ...],
) -> dict[str, str]:
    values = {item.file_id: item.relative_path for item in files}
    values.update(
        {item.symbol_id: item.location.relative_path for item in symbols}
    )
    values.update({item.module_id: item.relative_path for item in modules})
    return values


def _take(
    values: Iterable[str],
    maximum: int,
) -> tuple[tuple[str, ...], bool]:
    ordered = tuple(sorted(set(values)))
    return ordered[:maximum], len(ordered) > maximum


def _bounded(value: int, name: str, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
