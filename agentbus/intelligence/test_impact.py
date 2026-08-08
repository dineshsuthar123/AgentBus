from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from agentbus.intelligence.errors import QueryLimitError
from agentbus.intelligence.identities import test_impact_result_id
from agentbus.intelligence.models import (
    DependencyKind,
    ImpactRequest,
    ImpactRisk,
    Module,
    Project,
    SourceFile,
    Symbol,
    SymbolKind,
    TestImpactResult,
    _relative_path,
)
from agentbus.intelligence.traversal import DependencyGraph, TraversalLimits


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NAMING_PART = re.compile(r"[a-z0-9]+")
_NAMING_NOISE = {
    "check",
    "checks",
    "spec",
    "specs",
    "test",
    "tests",
}


@dataclass(frozen=True)
class HistoricalTestFixture:
    fixture_id: str
    test_paths: tuple[str, ...]
    related_paths: tuple[str, ...] = ()
    related_symbol_ids: tuple[str, ...] = ()
    confidence: float = 0.8
    deterministic: bool = True

    def __post_init__(self) -> None:
        if (
            not self.fixture_id
            or len(self.fixture_id) > 256
            or re.fullmatch(r"[A-Za-z0-9_.-]+", self.fixture_id) is None
        ):
            raise ValueError("fixture_id must contain between 1 and 256 characters")
        if not self.test_paths or len(self.test_paths) > 2_000:
            raise ValueError("historical fixture requires 1 to 2000 test paths")
        if len(self.related_paths) > 1_000:
            raise ValueError("historical fixture has too many related paths")
        if len(self.related_symbol_ids) > 1_000:
            raise ValueError("historical fixture has too many related symbols")
        if not self.related_paths and not self.related_symbol_ids:
            raise ValueError("historical fixture requires related subjects")
        if self.confidence < 0 or self.confidence > 1:
            raise ValueError("historical fixture confidence must be between 0 and 1")
        object.__setattr__(
            self,
            "test_paths",
            tuple(_relative_path(path) for path in self.test_paths),
        )
        object.__setattr__(
            self,
            "related_paths",
            tuple(_relative_path(path) for path in self.related_paths),
        )
        for symbol_id in self.related_symbol_ids:
            if not symbol_id or len(symbol_id) > 256:
                raise ValueError("historical fixture symbol identity is invalid")


@dataclass(frozen=True)
class TestSelectionLimits:
    maximum_tests: int = 2_000
    maximum_evidence: int = 2_000
    maximum_historical_fixtures: int = 256
    maximum_graph_edges: int = 100_000
    full_suite_confidence_threshold: float = 0.65

    def __post_init__(self) -> None:
        _bounded(self.maximum_tests, "maximum_tests", 1, 2_000)
        _bounded(self.maximum_evidence, "maximum_evidence", 1, 2_000)
        _bounded(
            self.maximum_historical_fixtures,
            "maximum_historical_fixtures",
            0,
            2_000,
        )
        _bounded(
            self.maximum_graph_edges,
            "maximum_graph_edges",
            1,
            1_000_000,
        )
        if (
            self.full_suite_confidence_threshold < 0
            or self.full_suite_confidence_threshold > 1
        ):
            raise ValueError(
                "full_suite_confidence_threshold must be between 0 and 1"
            )


@dataclass
class _TestCandidate:
    path: str
    score: float = 0.0
    confidence: float = 0.0
    mandatory: bool = False
    indexed: bool = True
    reasons: set[str] = field(default_factory=set)


class TestImpactSelector:
    """Select deterministic test evidence without replacing verification policy."""

    def __init__(
        self,
        graph: DependencyGraph,
        *,
        projects: Iterable[Project] = (),
        files: Iterable[SourceFile] | None = None,
        symbols: Iterable[Symbol] | None = None,
        limits: TestSelectionLimits | None = None,
    ) -> None:
        self.limits = limits or TestSelectionLimits()
        self.projects = tuple(
            sorted(projects, key=lambda item: item.project_id)
        )
        all_files = tuple(graph.files if files is None else files)
        protected_file_ids = {
            item.file_id for item in all_files if item.protected
        }
        self._protected_paths = {
            item.relative_path for item in all_files if item.protected
        }
        self._all_files_by_path = {
            item.relative_path: item for item in all_files
        }
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
        all_symbols = tuple(graph.symbols if symbols is None else symbols)
        self._protected_symbol_ids = {
            item.symbol_id
            for item in all_symbols
            if item.file_id in protected_file_ids
        }
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
        self.modules = tuple(
            item
            for item in graph.modules
            if item.relative_path not in self._protected_paths
        )
        protected_node_ids = protected_file_ids | self._protected_symbol_ids
        protected_node_ids.update(
            item.module_id
            for item in graph.modules
            if item not in self.modules
        )
        safe_edges = tuple(
            edge
            for edge in graph.edges
            if (
                edge.source_id not in protected_node_ids
                and edge.target_id not in protected_node_ids
            )
        )
        self._graph_truncated = (
            len(safe_edges) > self.limits.maximum_graph_edges
        )
        safe_edges = safe_edges[: self.limits.maximum_graph_edges]
        self.graph = DependencyGraph(
            safe_edges,
            files=self.files,
            symbols=self.symbols,
            modules=self.modules,
            limits=TraversalLimits(
                maximum_depth=graph.limits.maximum_depth,
                maximum_nodes=graph.limits.maximum_nodes,
                maximum_edges=min(
                    graph.limits.maximum_edges,
                    self.limits.maximum_graph_edges,
                ),
            ),
        )
        self._files_by_path = {
            item.relative_path: item for item in self.files
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
        self._test_nodes: dict[str, str] = {}
        self._test_projects: dict[str, str | None] = {}
        for source in self.files:
            if source.test:
                self._test_nodes[source.file_id] = source.relative_path
                self._test_projects[source.relative_path] = source.project_id
        for symbol in self.symbols:
            if symbol.test or symbol.kind == SymbolKind.TEST:
                self._test_nodes[symbol.symbol_id] = symbol.location.relative_path
                self._test_projects.setdefault(
                    symbol.location.relative_path,
                    symbol.project_id,
                )
        self._known_test_paths = set(self._test_projects)

    def select(
        self,
        request: ImpactRequest,
        *,
        snapshot_id: str | None = None,
        affected_node_ids: Iterable[str] = (),
        direct_dependent_ids: Iterable[str] = (),
        affected_project_ids: Iterable[str] = (),
        affected_configuration_ids: Iterable[str] = (),
        configured_mandatory_tests: Iterable[str] = (),
        historical_fixtures: Iterable[HistoricalTestFixture] = (),
        subject_paths: Iterable[str] | None = None,
        subject_symbol_ids: Iterable[str] | None = None,
        risk: ImpactRisk = ImpactRisk.LOW,
        impact_confidence: float = 1.0,
        impact_truncated: bool = False,
    ) -> TestImpactResult:
        if impact_confidence < 0 or impact_confidence > 1:
            raise ValueError("impact_confidence must be between 0 and 1")
        paths = (
            request.paths
            if subject_paths is None
            else _bounded_paths(subject_paths, 1_000, "subject paths")
        )
        symbol_ids = (
            request.symbol_ids
            if subject_symbol_ids is None
            else _bounded_ids(
                subject_symbol_ids,
                1_000,
                "subject symbols",
            )
        )
        protected_subject_omitted = bool(
            set(paths).intersection(self._protected_paths)
            or set(symbol_ids).intersection(self._protected_symbol_ids)
        )
        paths = tuple(
            path for path in paths if path not in self._protected_paths
        )
        symbol_ids = tuple(
            symbol_id
            for symbol_id in symbol_ids
            if symbol_id not in self._protected_symbol_ids
        )
        affected = _bounded_ids(affected_node_ids, 10_000, "affected nodes")
        direct = _bounded_ids(
            direct_dependent_ids,
            5_000,
            "direct dependent nodes",
        )
        projects = _bounded_ids(
            affected_project_ids,
            1_000,
            "affected projects",
        )
        configurations = _bounded_ids(
            affected_configuration_ids,
            2_000,
            "affected configurations",
        )
        mandatory_paths = tuple(
            dict.fromkeys(
                _relative_path(path) for path in configured_mandatory_tests
            )
        )
        if len(mandatory_paths) > self.limits.maximum_tests:
            raise QueryLimitError(
                "Configured mandatory tests exceed the selection limit."
            )
        fixtures = tuple(historical_fixtures)
        if len(fixtures) > self.limits.maximum_historical_fixtures:
            raise QueryLimitError(
                "Historical test fixtures exceed the selection limit."
            )
        for path in mandatory_paths:
            source = self._all_files_by_path.get(path)
            if source is not None and source.protected:
                raise ValueError(
                    "A configured mandatory test resolves to a protected path."
                )

        candidates: dict[str, _TestCandidate] = {}
        escalation: list[str] = []
        if protected_subject_omitted:
            escalation.append("protected_test_subject_omitted")
        if self._graph_truncated:
            impact_truncated = True
            escalation.append("test_graph_edge_limit_reached")
        starts, unknown_subject = self._subjects(paths, symbol_ids)
        if unknown_subject:
            escalation.append("changed_subject_not_indexed")
        affected_nodes = set(affected)
        direct_nodes = set(direct)
        if not affected_nodes and starts and request.max_depth > 0:
            try:
                traversal = self.graph.transitive_dependents(
                    starts,
                    max_depth=request.max_depth,
                )
            except QueryLimitError:
                impact_truncated = True
                escalation.append("test_dependency_traversal_limit_reached")
            else:
                affected_nodes.update(traversal.node_ids)
                if traversal.truncated:
                    impact_truncated = True
        if not direct_nodes:
            for start_id in starts:
                try:
                    adjacent = self.graph.dependents(start_id)
                except QueryLimitError:
                    impact_truncated = True
                    escalation.append("test_dependency_query_limit_reached")
                    break
                direct_nodes.update(edge.source_id for edge in adjacent)
        affected_nodes.update(starts)

        for path in mandatory_paths:
            self._add(
                candidates,
                path,
                score=1_000.0,
                confidence=1.0,
                reason="configured_mandatory",
                mandatory=True,
                indexed=path in self._known_test_paths,
            )
            if path not in self._known_test_paths:
                escalation.append("mandatory_test_not_indexed")

        for path in paths:
            source = self._files_by_path.get(path)
            if source is not None and source.test:
                self._add(
                    candidates,
                    path,
                    score=950.0,
                    confidence=1.0,
                    reason="changed_test",
                    mandatory=True,
                )

        for edge in self.graph.edges:
            test_path = self._test_nodes.get(edge.source_id)
            if test_path is None or edge.target_id not in starts:
                continue
            if edge.kind == DependencyKind.TESTS:
                reason = "direct_test_reference"
                confidence = edge.confidence
            elif edge.kind == DependencyKind.IMPORTS:
                reason = "direct_test_import"
                confidence = min(edge.confidence, 0.95)
            else:
                continue
            self._add(
                candidates,
                test_path,
                score=900.0,
                confidence=confidence,
                reason=reason,
                mandatory=True,
            )

        for node_id in sorted(affected_nodes | direct_nodes):
            path = self._test_nodes.get(node_id)
            if path is None:
                continue
            is_direct = node_id in direct_nodes
            self._add(
                candidates,
                path,
                score=850.0 if is_direct else 700.0,
                confidence=0.9 if is_direct else 0.8,
                reason=(
                    "direct_dependency_path"
                    if is_direct
                    else "transitive_dependency_path"
                ),
                mandatory=is_direct,
            )

        changed_paths = set(paths)
        changed_symbols = set(symbol_ids)
        for fixture in sorted(fixtures, key=lambda item: item.fixture_id):
            if not fixture.deterministic:
                continue
            if not (
                changed_paths.intersection(fixture.related_paths)
                or changed_symbols.intersection(fixture.related_symbol_ids)
            ):
                continue
            for path in fixture.test_paths:
                source = self._all_files_by_path.get(path)
                if source is not None and source.protected:
                    escalation.append("protected_historical_test_omitted")
                    continue
                self._add(
                    candidates,
                    path,
                    score=650.0,
                    confidence=fixture.confidence,
                    reason=f"historical_fixture:{fixture.fixture_id}",
                    indexed=path in self._known_test_paths,
                )

        project_ids = set(projects)
        project_ids.update(
            project_id
            for node_id in affected_nodes
            if (project_id := self._node_projects.get(node_id)) is not None
        )
        for path, project_id in sorted(self._test_projects.items()):
            if project_id is not None and project_id in project_ids:
                self._add(
                    candidates,
                    path,
                    score=400.0,
                    confidence=0.6,
                    reason="affected_project",
                )

        broad_configuration = self._add_configuration_tests(
            candidates,
            configurations,
            paths,
            project_ids,
        )
        if broad_configuration:
            escalation.append("configuration_change_has_broad_reach")

        naming_terms = self._subject_terms(paths, symbol_ids)
        for path in sorted(self._known_test_paths):
            if naming_terms.intersection(_name_terms(path)):
                self._add(
                    candidates,
                    path,
                    score=200.0,
                    confidence=0.35,
                    reason="naming_convention_fallback",
                )

        mandatory_candidates = tuple(
            sorted(
                (item for item in candidates.values() if item.mandatory),
                key=lambda item: (
                    "configured_mandatory" not in item.reasons,
                    -item.score,
                    item.path.casefold(),
                ),
            )
        )
        selected_mandatory = mandatory_candidates[: self.limits.maximum_tests]
        mandatory_truncated = len(mandatory_candidates) > len(
            selected_mandatory
        )
        if mandatory_truncated:
            escalation.append("mandatory_test_evidence_truncated")
        optional_candidates = tuple(
            sorted(
                (item for item in candidates.values() if not item.mandatory),
                key=lambda item: (-item.score, item.path.casefold()),
            )
        )
        remaining = self.limits.maximum_tests - len(selected_mandatory)
        selected_candidates = (
            selected_mandatory + optional_candidates[:remaining]
        )
        selection_truncated = (
            mandatory_truncated or len(optional_candidates) > remaining
        )
        if selection_truncated:
            escalation.append("test_selection_truncated")
        if any(not item.indexed for item in selected_candidates):
            escalation.append("selected_test_not_indexed")

        selected = tuple(sorted(item.path for item in selected_candidates))
        mandatory = tuple(sorted(item.path for item in selected_mandatory))
        optional = tuple(sorted(set(selected) - set(mandatory)))
        selected_reasons = {
            reason
            for item in selected_candidates
            for reason in item.reasons
        }
        fallback_only = bool(selected_reasons) and selected_reasons <= {
            "affected_project",
            "naming_convention_fallback",
        }
        confidence = _selection_confidence(
            selected_candidates,
            impact_confidence=impact_confidence,
            unknown_subject=unknown_subject,
            truncated=impact_truncated or selection_truncated,
        )
        if fallback_only:
            confidence = min(
                confidence,
                0.4
                if selected_reasons == {"naming_convention_fallback"}
                else 0.6,
            )
        if confidence < self.limits.full_suite_confidence_threshold:
            escalation.append("low_test_selection_confidence")
        if impact_truncated:
            escalation.append("impact_analysis_truncated")
        if risk == ImpactRisk.HIGH:
            escalation.append("high_change_risk")
        elif risk == ImpactRisk.CRITICAL:
            escalation.append("critical_change_risk")
        if not selected:
            escalation.append("no_relevant_tests_identified")
        if fallback_only:
            escalation.append("only_fallback_test_evidence")

        full_suite = bool(escalation) or risk in {
            ImpactRisk.HIGH,
            ImpactRisk.CRITICAL,
        }
        evidence = [
            "test.safety.verification_policy_authoritative",
            "test.safety.selection_is_not_completeness_proof",
        ]
        evidence.extend(
            "test.selection:"
            f"{item.path}:{item.confidence:.6f}:"
            f"{','.join(sorted(item.reasons))}"
            for item in selected_candidates
        )
        evidence_values = tuple(dict.fromkeys(evidence))
        if len(evidence_values) > self.limits.maximum_evidence:
            evidence_values = evidence_values[: self.limits.maximum_evidence]
            full_suite = True
            escalation.append("test_evidence_truncated")
        return TestImpactResult(
            result_id=test_impact_result_id(
                snapshot_id,
                paths,
                symbol_ids,
            ),
            selected_tests=selected,
            mandatory_tests=mandatory,
            optional_tests=optional,
            full_suite_recommended=full_suite,
            confidence=confidence,
            evidence=evidence_values,
            escalation_reasons=tuple(dict.fromkeys(escalation))[:256],
        )

    def _subjects(
        self,
        paths: tuple[str, ...],
        symbol_ids: tuple[str, ...],
    ) -> tuple[set[str], bool]:
        starts: set[str] = set()
        unknown = False
        for path in paths:
            source = self._files_by_path.get(path)
            if source is None:
                unknown = True
                continue
            starts.add(source.file_id)
            starts.update(
                symbol.symbol_id
                for symbol in self._symbols_by_file.get(source.file_id, ())
            )
        for symbol_id in symbol_ids:
            if symbol_id not in self._symbols_by_id:
                unknown = True
                continue
            starts.add(symbol_id)
        return starts, unknown

    def _add_configuration_tests(
        self,
        candidates: dict[str, _TestCandidate],
        configuration_ids: tuple[str, ...],
        changed_paths: tuple[str, ...],
        affected_projects: set[str],
    ) -> bool:
        configuration_projects = {
            project_id
            for node_id in configuration_ids
            if (project_id := self._node_projects.get(node_id)) is not None
        }
        broad = False
        for path in changed_paths:
            source = self._files_by_path.get(path)
            if source is None or not _configuration_path(path):
                continue
            if source.project_id is None or "/" not in path:
                broad = True
            elif source.project_id is not None:
                configuration_projects.add(source.project_id)
        if configuration_ids and not configuration_projects:
            broad = True
        target_projects = configuration_projects or affected_projects
        for path, project_id in sorted(self._test_projects.items()):
            if broad or (
                project_id is not None and project_id in target_projects
            ):
                self._add(
                    candidates,
                    path,
                    score=500.0,
                    confidence=0.65,
                    reason="configuration_relationship",
                )
        return broad

    def _subject_terms(
        self,
        paths: tuple[str, ...],
        symbol_ids: tuple[str, ...],
    ) -> set[str]:
        terms = {term for path in paths for term in _name_terms(path)}
        for symbol_id in symbol_ids:
            symbol = self._symbols_by_id.get(symbol_id)
            if symbol is not None:
                terms.update(_name_terms(symbol.name))
        return terms

    @staticmethod
    def _add(
        candidates: dict[str, _TestCandidate],
        path: str,
        *,
        score: float,
        confidence: float,
        reason: str,
        mandatory: bool = False,
        indexed: bool = True,
    ) -> None:
        normalized = _relative_path(path)
        candidate = candidates.get(normalized)
        if candidate is None:
            candidate = _TestCandidate(path=normalized)
            candidates[normalized] = candidate
        candidate.score = max(candidate.score, score)
        candidate.confidence = max(candidate.confidence, confidence)
        candidate.mandatory = candidate.mandatory or mandatory
        candidate.indexed = candidate.indexed and indexed
        candidate.reasons.add(reason)


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


def _name_terms(value: str) -> set[str]:
    stem = PurePosixPath(value).stem
    name = _CAMEL_BOUNDARY.sub(" ", stem).casefold()
    return {
        part
        for part in _NAMING_PART.findall(name)
        if len(part) >= 3 and part not in _NAMING_NOISE
    }


def _configuration_path(path: str) -> bool:
    name = PurePosixPath(path).name.casefold()
    return (
        name.startswith(("config.", "settings."))
        or name.endswith((".json", ".toml", ".yaml", ".yml"))
        or name
        in {
            ".editorconfig",
            "application.properties",
            "build.gradle",
            "build.gradle.kts",
            "go.mod",
            "go.work",
            "gradle.properties",
            "pom.xml",
        }
    )


def _selection_confidence(
    candidates: tuple[_TestCandidate, ...],
    *,
    impact_confidence: float,
    unknown_subject: bool,
    truncated: bool,
) -> float:
    evidence_confidence = (
        sum(item.confidence for item in candidates) / len(candidates)
        if candidates
        else 0.2
    )
    confidence = (evidence_confidence + impact_confidence) / 2
    if unknown_subject:
        confidence -= 0.2
    if truncated:
        confidence -= 0.2
    return round(max(0.05, min(1.0, confidence)), 6)


def _bounded_ids(
    values: Iterable[str],
    maximum: int,
    name: str,
) -> tuple[str, ...]:
    records = tuple(dict.fromkeys(values))
    if len(records) > maximum:
        raise QueryLimitError(f"{name} exceed the selection limit")
    if any(not value or len(value) > 2_048 for value in records):
        raise ValueError(f"{name} contain an invalid identity")
    return records


def _bounded_paths(
    values: Iterable[str],
    maximum: int,
    name: str,
) -> tuple[str, ...]:
    records = tuple(
        dict.fromkeys(_relative_path(value) for value in values)
    )
    if len(records) > maximum:
        raise QueryLimitError(f"{name} exceed the selection limit")
    return records


def _bounded(value: int, name: str, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
