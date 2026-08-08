from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field

from agentbus.intelligence.discovery.ignore import glob_match
from agentbus.intelligence.errors import QueryLimitError
from agentbus.intelligence.models import (
    ArchitectureBoundary,
    DependencyEdge,
    DependencyKind,
    SearchQuery,
    SearchResult,
    SourceFile,
    Symbol,
    SymbolKind,
    _relative_path,
)
from agentbus.intelligence.search import RepositoryLexicalIndex
from agentbus.intelligence.semantic import OptionalSemanticSearch
from agentbus.intelligence.traversal import DependencyGraph


@dataclass(frozen=True)
class HybridRankingWeights:
    lexical: float = 0.65
    semantic: float = 0.30
    symbol_match: float = 20.0
    dependency: float = 24.0
    project: float = 6.0
    architecture: float = 6.0
    test_relationship: float = 12.0
    recent_change: float = 4.0

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if value < 0 or value > 1_000:
                raise ValueError(f"{name} weight must be between 0 and 1000")


@dataclass(frozen=True)
class HybridRetrievalLimits:
    maximum_initial_results: int = 200
    maximum_anchor_symbols: int = 4
    maximum_dependency_depth: int = 4
    maximum_graph_nodes: int = 2_000
    maximum_graph_edges: int = 100_000
    maximum_recent_paths: int = 1_000

    def __post_init__(self) -> None:
        _bounded(
            self.maximum_initial_results,
            "maximum_initial_results",
            1,
            200,
        )
        _bounded(
            self.maximum_anchor_symbols,
            "maximum_anchor_symbols",
            1,
            32,
        )
        _bounded(
            self.maximum_dependency_depth,
            "maximum_dependency_depth",
            0,
            16,
        )
        _bounded(
            self.maximum_graph_nodes,
            "maximum_graph_nodes",
            1,
            100_000,
        )
        _bounded(
            self.maximum_graph_edges,
            "maximum_graph_edges",
            1,
            1_000_000,
        )
        _bounded(
            self.maximum_recent_paths,
            "maximum_recent_paths",
            1,
            10_000,
        )


@dataclass(frozen=True)
class _GraphMatch:
    node_ids: tuple[str, ...]
    edges: tuple[DependencyEdge, ...]
    confidence: float

    @property
    def distance(self) -> int:
        return len(self.edges)


@dataclass
class _Candidate:
    source: SourceFile
    symbol: Symbol | None
    components: dict[str, float] = field(default_factory=dict)
    matched_terms: set[str] = field(default_factory=set)
    dependency_path: tuple[str, ...] = ()
    dependency_match: _GraphMatch | None = None

    @property
    def identity(self) -> str:
        return (
            self.symbol.symbol_id
            if self.symbol is not None
            else self.source.file_id
        )


class HybridRetriever:
    """Fuse lexical, graph, architecture, and optional semantic evidence."""

    def __init__(
        self,
        lexical: RepositoryLexicalIndex,
        graph: DependencyGraph,
        files: Iterable[SourceFile],
        symbols: Iterable[Symbol],
        *,
        boundaries: Iterable[ArchitectureBoundary] = (),
        semantic: OptionalSemanticSearch | None = None,
        weights: HybridRankingWeights | None = None,
        limits: HybridRetrievalLimits | None = None,
    ) -> None:
        self.lexical = lexical
        self.graph = graph
        self.semantic = semantic
        self.weights = weights or HybridRankingWeights()
        self.limits = limits or HybridRetrievalLimits()
        if len(graph.edges) > self.limits.maximum_graph_edges:
            raise QueryLimitError(
                "Hybrid retrieval graph exceeds the configured edge limit."
            )
        self.files = tuple(sorted(files, key=lambda item: item.file_id))
        self.symbols = tuple(
            sorted(symbols, key=lambda item: item.symbol_id)
        )
        self.boundaries = tuple(
            sorted(boundaries, key=lambda item: item.boundary_id)
        )
        self._files_by_id = {item.file_id: item for item in self.files}
        self._files_by_path = {
            item.relative_path: item for item in self.files
        }
        self._symbols_by_id = {
            item.symbol_id: item for item in self.symbols
        }
        self._node_candidates = self._candidate_nodes()
        self._adjacency = self._graph_adjacency()

    def search(
        self,
        query: SearchQuery,
        *,
        stale: bool = False,
        recent_paths: Iterable[str] = (),
    ) -> tuple[SearchResult, ...]:
        query = SearchQuery.model_validate(query.model_dump(mode="python"))
        initial_query = query.model_copy(
            update={
                "limit": self.limits.maximum_initial_results,
                "offset": 0,
            }
        )
        lexical_results = self.lexical.search(
            initial_query,
            stale=stale,
        )
        semantic_results = (
            self.semantic.search(initial_query, stale=stale)
            if self.semantic is not None
            else ()
        )
        candidates: dict[str, _Candidate] = {}
        for result in lexical_results:
            candidate = self._merge_result(
                candidates,
                result,
                query,
            )
            if candidate is None:
                continue
            candidate.components["lexical"] = max(
                candidate.components.get("lexical", 0.0),
                round(result.score * self.weights.lexical, 6),
            )
            exact = max(
                (
                    value
                    for name, value in result.score_components.items()
                    if name.startswith("exact_")
                ),
                default=0.0,
            )
            if exact:
                candidate.components["symbol_match"] = max(
                    candidate.components.get("symbol_match", 0.0),
                    round(
                        min(exact / 120.0, 1.0)
                        * self.weights.symbol_match,
                        6,
                    ),
                )
            candidate.matched_terms.update(result.matched_terms)

        for result in semantic_results:
            candidate = self._merge_result(
                candidates,
                result,
                query,
            )
            if candidate is None:
                continue
            candidate.components["semantic"] = max(
                candidate.components.get("semantic", 0.0),
                round(result.score * self.weights.semantic, 6),
            )

        anchors = self._anchors(lexical_results, semantic_results)
        graph_matches = self._graph_matches(anchors)
        for node_id, graph_match in graph_matches.items():
            record = self._node_candidates.get(node_id)
            if record is None:
                continue
            source, symbol = record
            if not _matches_filters(source, symbol, query):
                continue
            candidate = candidates.setdefault(
                _result_identity(source, symbol),
                _Candidate(source=source, symbol=symbol),
            )
            if (
                candidate.dependency_match is None
                or _graph_match_key(graph_match)
                < _graph_match_key(candidate.dependency_match)
            ):
                candidate.dependency_match = graph_match
                candidate.dependency_path = graph_match.node_ids

        anchor_projects = {
            self._symbols_by_id[item].project_id
            for item in anchors
            if (
                item in self._symbols_by_id
                and self._symbols_by_id[item].project_id is not None
            )
        }
        anchor_boundaries = {
            boundary.boundary_id
            for anchor in anchors
            for boundary in self._boundaries_for_symbol(anchor)
        }
        normalized_recent: set[str] = set()
        for index, path in enumerate(recent_paths):
            if index >= self.limits.maximum_recent_paths:
                raise QueryLimitError(
                    "Recent-path evidence exceeded the configured limit."
                )
            normalized_recent.add(_relative_path(path))
        for candidate in candidates.values():
            self._apply_graph_score(candidate)
            if (
                candidate.source.project_id is not None
                and candidate.source.project_id in anchor_projects
            ):
                candidate.components["project_proximity"] = (
                    self.weights.project
                )
            shared_boundaries = tuple(
                boundary
                for boundary in self._boundaries_for_path(
                    candidate.source.relative_path
                )
                if boundary.boundary_id in anchor_boundaries
            )
            if shared_boundaries:
                candidate.components["architecture"] = round(
                    max(item.confidence for item in shared_boundaries)
                    * self.weights.architecture,
                    6,
                )
            if candidate.source.relative_path in normalized_recent:
                candidate.components["recent_change"] = (
                    self.weights.recent_change
                )

        ranked = tuple(
            candidate
            for candidate in candidates.values()
            if candidate.components
        )
        ordered = sorted(
            ranked,
            key=lambda item: (
                -sum(item.components.values()),
                item.source.relative_path.casefold(),
                (
                    item.symbol.qualified_name.casefold()
                    if item.symbol is not None
                    else ""
                ),
                item.identity,
            ),
        )
        selected = ordered[query.offset : query.offset + query.limit]
        return tuple(
            SearchResult(
                rank=query.offset + index + 1,
                score=round(sum(item.components.values()), 6),
                score_components=dict(sorted(item.components.items())),
                matched_terms=tuple(sorted(item.matched_terms)),
                relative_path=item.source.relative_path,
                source_hash=item.source.content_hash,
                project_id=item.source.project_id,
                symbol=item.symbol,
                dependency_path=item.dependency_path,
                stale=stale,
                explanation=_explanation(item.components),
            )
            for index, item in enumerate(selected)
        )

    def _merge_result(
        self,
        candidates: dict[str, _Candidate],
        result: SearchResult,
        query: SearchQuery,
    ) -> _Candidate | None:
        source = self._files_by_path.get(result.relative_path)
        if (
            source is None
            or source.protected
            or source.content_hash != result.source_hash
        ):
            return None
        symbol = None
        if result.symbol is not None:
            symbol = self._symbols_by_id.get(result.symbol.symbol_id)
            if symbol is None or symbol.file_id != source.file_id:
                return None
        if not _matches_filters(source, symbol, query):
            return None
        identity = _result_identity(source, symbol)
        return candidates.setdefault(
            identity,
            _Candidate(source=source, symbol=symbol),
        )

    def _anchors(
        self,
        lexical_results: tuple[SearchResult, ...],
        semantic_results: tuple[SearchResult, ...],
    ) -> tuple[str, ...]:
        anchors: list[str] = []
        for result in (*lexical_results, *semantic_results):
            if result.symbol is None:
                continue
            symbol = self._symbols_by_id.get(result.symbol.symbol_id)
            if (
                symbol is None
                or symbol.file_id
                not in self._files_by_id
                or symbol.symbol_id in anchors
            ):
                continue
            anchors.append(symbol.symbol_id)
            if len(anchors) >= self.limits.maximum_anchor_symbols:
                break
        return tuple(anchors)

    def _graph_matches(
        self,
        anchors: tuple[str, ...],
    ) -> dict[str, _GraphMatch]:
        best: dict[str, _GraphMatch] = {}
        inspected_edges = 0
        edge_limit_reached = False
        for anchor in anchors:
            queue: deque[
                tuple[
                    str,
                    tuple[str, ...],
                    tuple[DependencyEdge, ...],
                    float,
                ]
            ] = deque(((anchor, (anchor,), (), 1.0),))
            visited = {anchor}
            while queue and len(best) < self.limits.maximum_graph_nodes:
                node_id, node_path, edge_path, confidence = queue.popleft()
                if len(edge_path) >= self.limits.maximum_dependency_depth:
                    continue
                for neighbor, edge in self._adjacency.get(node_id, ()):
                    inspected_edges += 1
                    if inspected_edges > self.limits.maximum_graph_edges:
                        edge_limit_reached = True
                        break
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    next_edges = (*edge_path, edge)
                    match = _GraphMatch(
                        node_ids=(*node_path, neighbor),
                        edges=next_edges,
                        confidence=min(confidence, edge.confidence),
                    )
                    current = best.get(neighbor)
                    if (
                        current is None
                        or _graph_match_key(match)
                        < _graph_match_key(current)
                    ):
                        best[neighbor] = match
                    queue.append(
                        (
                            neighbor,
                            match.node_ids,
                            match.edges,
                            match.confidence,
                        )
                    )
                if edge_limit_reached:
                    break
            if (
                edge_limit_reached
                or len(best) >= self.limits.maximum_graph_nodes
            ):
                break
        return best

    def _apply_graph_score(self, candidate: _Candidate) -> None:
        match = candidate.dependency_match
        if match is None or match.distance == 0:
            return
        candidate.components["dependency"] = round(
            self.weights.dependency
            * match.confidence
            / match.distance,
            6,
        )
        if any(
            edge.kind == DependencyKind.TESTS for edge in match.edges
        ):
            candidate.components["test_relationship"] = (
                self.weights.test_relationship
            )

    def _candidate_nodes(
        self,
    ) -> dict[str, tuple[SourceFile, Symbol | None]]:
        candidates: dict[str, tuple[SourceFile, Symbol | None]] = {}
        for source in self.files:
            candidates[source.file_id] = (source, None)
        for symbol in self.symbols:
            source = self._files_by_id.get(symbol.file_id)
            if source is not None:
                candidates[symbol.symbol_id] = (source, symbol)
        for module in self.graph.modules:
            source = self._files_by_path.get(module.relative_path)
            if source is not None:
                candidates[module.module_id] = (source, None)
        return candidates

    def _graph_adjacency(
        self,
    ) -> dict[str, tuple[tuple[str, DependencyEdge], ...]]:
        adjacency: dict[str, list[tuple[str, DependencyEdge]]] = defaultdict(list)
        for edge in self.graph.edges:
            if not edge.resolved:
                continue
            adjacency[edge.source_id].append((edge.target_id, edge))
            adjacency[edge.target_id].append((edge.source_id, edge))
        return {
            node_id: tuple(
                sorted(
                    neighbors,
                    key=lambda item: (item[1].edge_id, item[0]),
                )
            )
            for node_id, neighbors in adjacency.items()
        }

    def _boundaries_for_symbol(
        self,
        symbol_id: str,
    ) -> tuple[ArchitectureBoundary, ...]:
        symbol = self._symbols_by_id.get(symbol_id)
        if symbol is None:
            return ()
        return self._boundaries_for_path(symbol.location.relative_path)

    def _boundaries_for_path(
        self,
        relative_path: str,
    ) -> tuple[ArchitectureBoundary, ...]:
        return tuple(
            boundary
            for boundary in self.boundaries
            if any(
                glob_match(relative_path, scope)
                for scope in boundary.scope
            )
        )


def _matches_filters(
    source: SourceFile,
    symbol: Symbol | None,
    query: SearchQuery,
) -> bool:
    if source.protected:
        return False
    if query.project_ids and source.project_id not in query.project_ids:
        return False
    if query.languages and source.language not in query.languages:
        return False
    if query.symbol_kinds and (
        symbol is None or symbol.kind not in query.symbol_kinds
    ):
        return False
    if query.path_prefixes and not any(
        not prefix
        or source.relative_path == prefix
        or source.relative_path.startswith(f"{prefix}/")
        for prefix in query.path_prefixes
    ):
        return False
    if query.test_only and not (
        source.test
        or (
            symbol is not None
            and (symbol.test or symbol.kind == SymbolKind.TEST)
        )
    ):
        return False
    return True


def _result_identity(
    source: SourceFile,
    symbol: Symbol | None,
) -> str:
    return symbol.symbol_id if symbol is not None else source.file_id


def _graph_match_key(
    match: _GraphMatch,
) -> tuple[int, float, tuple[str, ...]]:
    return (
        match.distance,
        -match.confidence,
        match.node_ids,
    )


def _explanation(components: dict[str, float]) -> str:
    ordered = sorted(
        components.items(),
        key=lambda item: (-item[1], item[0]),
    )
    evidence = ", ".join(
        f"{name.replace('_', ' ')}={score:g}"
        for name, score in ordered
    )
    return f"Explainable hybrid ranking: {evidence}."


def _bounded(
    value: int,
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
