from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentbus.intelligence.errors import QueryLimitError
from agentbus.intelligence.models import (
    DependencyEdge,
    DependencyKind,
    Module,
    SourceFile,
    Symbol,
)

if TYPE_CHECKING:
    from agentbus.intelligence.storage import IndexStore


@dataclass(frozen=True)
class TraversalLimits:
    maximum_depth: int = 16
    maximum_nodes: int = 10_000
    maximum_edges: int = 100_000

    def __post_init__(self) -> None:
        if self.maximum_depth < 0 or self.maximum_depth > 64:
            raise ValueError("maximum_depth must be between 0 and 64")
        if self.maximum_nodes < 1 or self.maximum_nodes > 1_000_000:
            raise ValueError(
                "maximum_nodes must be between 1 and 1000000"
            )
        if self.maximum_edges < 1 or self.maximum_edges > 1_000_000:
            raise ValueError(
                "maximum_edges must be between 1 and 1000000"
            )


@dataclass(frozen=True)
class TraversalResult:
    node_ids: tuple[str, ...]
    edges: tuple[DependencyEdge, ...]
    maximum_depth_reached: int
    truncated: bool = False


@dataclass(frozen=True)
class DependencyPath:
    node_ids: tuple[str, ...]
    edges: tuple[DependencyEdge, ...]
    confidence: float


@dataclass(frozen=True)
class StrongComponent:
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    cyclic: bool


@dataclass(frozen=True)
class ProjectBoundaryCrossing:
    edge: DependencyEdge
    source_project_id: str
    target_project_id: str


@dataclass(frozen=True)
class CentralityScore:
    node_id: str
    inbound_edges: int
    outbound_edges: int
    score: float


class DependencyGraph:
    """Bounded deterministic queries over persisted dependency edges."""

    def __init__(
        self,
        edges: Iterable[DependencyEdge],
        *,
        symbols: Iterable[Symbol] = (),
        files: Iterable[SourceFile] = (),
        modules: Iterable[Module] = (),
        node_projects: Mapping[str, str] | None = None,
        limits: TraversalLimits | None = None,
    ) -> None:
        self.limits = limits or TraversalLimits()
        edge_records = tuple(
            sorted(edges, key=lambda item: item.edge_id)
        )
        if len(edge_records) > 1_000_000:
            raise QueryLimitError(
                "Repository graph exceeds the hard edge storage limit."
            )
        self.edges = edge_records
        self.symbols = tuple(
            sorted(symbols, key=lambda item: item.symbol_id)
        )
        self.files = tuple(
            sorted(files, key=lambda item: item.file_id)
        )
        self.modules = tuple(
            sorted(modules, key=lambda item: item.module_id)
        )
        forward: dict[str, list[DependencyEdge]] = defaultdict(list)
        reverse: dict[str, list[DependencyEdge]] = defaultdict(list)
        nodes: set[str] = set()
        for edge in self.edges:
            forward[edge.source_id].append(edge)
            reverse[edge.target_id].append(edge)
            nodes.update((edge.source_id, edge.target_id))
        nodes.update(item.symbol_id for item in self.symbols)
        nodes.update(item.file_id for item in self.files)
        nodes.update(item.module_id for item in self.modules)
        self.node_ids = tuple(sorted(nodes))
        self._forward = {
            key: tuple(value) for key, value in forward.items()
        }
        self._reverse = {
            key: tuple(value) for key, value in reverse.items()
        }
        projects = dict(node_projects or {})
        projects.update(
            {
                item.file_id: item.project_id
                for item in self.files
                if item.project_id is not None
            }
        )
        projects.update(
            {
                item.module_id: item.project_id
                for item in self.modules
            }
        )
        projects.update(
            {
                item.symbol_id: item.project_id
                for item in self.symbols
                if item.project_id is not None
            }
        )
        self._node_projects = projects

    @classmethod
    def from_store(
        cls,
        store: IndexStore,
        snapshot_id: str,
        *,
        limits: TraversalLimits | None = None,
    ) -> DependencyGraph:
        return cls(
            store.list_edges(snapshot_id),
            symbols=store.list_symbols(snapshot_id),
            files=store.list_files(snapshot_id),
            modules=store.list_modules(snapshot_id),
            limits=limits,
        )

    def dependencies(
        self,
        node_id: str,
        *,
        kinds: Iterable[DependencyKind] = (),
        include_unresolved: bool = False,
    ) -> tuple[DependencyEdge, ...]:
        return self._direct(
            node_id,
            reverse=False,
            kinds=kinds,
            include_unresolved=include_unresolved,
        )

    def dependents(
        self,
        node_id: str,
        *,
        kinds: Iterable[DependencyKind] = (),
        include_unresolved: bool = False,
    ) -> tuple[DependencyEdge, ...]:
        return self._direct(
            node_id,
            reverse=True,
            kinds=kinds,
            include_unresolved=include_unresolved,
        )

    def transitive_dependencies(
        self,
        start_ids: str | Iterable[str],
        *,
        max_depth: int | None = None,
        kinds: Iterable[DependencyKind] = (),
        include_unresolved: bool = False,
    ) -> TraversalResult:
        return self._traverse(
            start_ids,
            reverse=False,
            max_depth=max_depth,
            kinds=kinds,
            include_unresolved=include_unresolved,
        )

    def transitive_dependents(
        self,
        start_ids: str | Iterable[str],
        *,
        max_depth: int | None = None,
        kinds: Iterable[DependencyKind] = (),
        include_unresolved: bool = False,
    ) -> TraversalResult:
        return self._traverse(
            start_ids,
            reverse=True,
            max_depth=max_depth,
            kinds=kinds,
            include_unresolved=include_unresolved,
        )

    def shortest_path(
        self,
        source_id: str,
        target_id: str,
        *,
        reverse: bool = False,
        max_depth: int | None = None,
        kinds: Iterable[DependencyKind] = (),
        include_unresolved: bool = False,
    ) -> DependencyPath | None:
        depth_limit = self._depth(max_depth)
        if source_id == target_id:
            return DependencyPath(
                node_ids=(source_id,),
                edges=(),
                confidence=1.0,
            )
        allowed = _kind_filter(kinds)
        queue: deque[tuple[str, int]] = deque(((source_id, 0),))
        parents: dict[str, tuple[str, DependencyEdge]] = {}
        visited = {source_id}
        adjacency = self._reverse if reverse else self._forward
        inspected_edges = 0
        while queue:
            current, depth = queue.popleft()
            if depth >= depth_limit:
                continue
            for edge in adjacency.get(current, ()):
                inspected_edges += 1
                if inspected_edges > self.limits.maximum_edges:
                    raise QueryLimitError(
                        "Shortest-path query reached the graph edge limit."
                    )
                if not _edge_allowed(edge, allowed, include_unresolved):
                    continue
                next_id = edge.source_id if reverse else edge.target_id
                if next_id in visited:
                    continue
                if len(visited) >= self.limits.maximum_nodes:
                    raise QueryLimitError(
                        "Shortest-path query reached the graph node limit."
                    )
                visited.add(next_id)
                parents[next_id] = (current, edge)
                if next_id == target_id:
                    return _reconstruct_path(
                        source_id,
                        target_id,
                        parents,
                    )
                queue.append((next_id, depth + 1))
        return None

    def strongly_connected_components(
        self,
        *,
        kinds: Iterable[DependencyKind] = (),
    ) -> tuple[StrongComponent, ...]:
        allowed = _kind_filter(kinds)
        edges = tuple(
            edge
            for edge in self.edges
            if edge.resolved and (not allowed or edge.kind in allowed)
        )
        nodes = tuple(
            sorted(
                {
                    identity
                    for edge in edges
                    for identity in (edge.source_id, edge.target_id)
                }
            )
        )
        if (
            len(nodes) > self.limits.maximum_nodes
            or len(edges) > self.limits.maximum_edges
        ):
            raise QueryLimitError(
                "Strong-component analysis exceeds graph query limits."
            )
        forward = _adjacency(edges, reverse=False)
        reverse = _adjacency(edges, reverse=True)
        finish_order = _finish_order(nodes, forward)
        assigned: set[str] = set()
        components: list[StrongComponent] = []
        for start in reversed(finish_order):
            if start in assigned:
                continue
            component = _collect_component(start, reverse, assigned)
            members = frozenset(component)
            component_edges = tuple(
                edge.edge_id
                for edge in edges
                if (
                    edge.source_id in members
                    and edge.target_id in members
                )
            )
            self_loop = any(
                edge.source_id == edge.target_id
                for edge in edges
                if edge.source_id in members
            )
            components.append(
                StrongComponent(
                    node_ids=tuple(sorted(component)),
                    edge_ids=component_edges,
                    cyclic=len(component) > 1 or self_loop,
                )
            )
        return tuple(
            sorted(components, key=lambda item: item.node_ids)
        )

    def cycles(
        self,
        *,
        kinds: Iterable[DependencyKind] = (),
    ) -> tuple[StrongComponent, ...]:
        return tuple(
            item
            for item in self.strongly_connected_components(kinds=kinds)
            if item.cyclic
        )

    def project_boundary_crossings(
        self,
    ) -> tuple[ProjectBoundaryCrossing, ...]:
        self._require_edge_budget("Project crossing")
        crossings: list[ProjectBoundaryCrossing] = []
        for edge in self.edges:
            if not edge.resolved:
                continue
            source = self._node_projects.get(edge.source_id)
            target = self._node_projects.get(edge.target_id)
            if source is None or target is None or source == target:
                continue
            crossings.append(
                ProjectBoundaryCrossing(
                    edge=edge,
                    source_project_id=source,
                    target_project_id=target,
                )
            )
        return tuple(crossings)

    def public_api_surfaces(self) -> tuple[str, ...]:
        return tuple(
            item.symbol_id
            for item in self.symbols
            if item.exported or item.endpoint is not None
        )

    def high_centrality(
        self,
        *,
        limit: int = 25,
    ) -> tuple[CentralityScore, ...]:
        if limit < 1 or limit > 1_000:
            raise ValueError("centrality limit must be between 1 and 1000")
        self._require_edge_budget("Centrality")
        node_count = max(len(self.node_ids), 1)
        scores: list[CentralityScore] = []
        for node_id in self.node_ids:
            inbound = sum(
                edge.resolved
                for edge in self._reverse.get(node_id, ())
            )
            outbound = sum(
                edge.resolved
                for edge in self._forward.get(node_id, ())
            )
            if not inbound and not outbound:
                continue
            scores.append(
                CentralityScore(
                    node_id=node_id,
                    inbound_edges=inbound,
                    outbound_edges=outbound,
                    score=(
                        (inbound + outbound)
                        / max(2 * (node_count - 1), 1)
                    ),
                )
            )
        return tuple(
            sorted(
                scores,
                key=lambda item: (
                    -item.score,
                    -item.inbound_edges,
                    -item.outbound_edges,
                    item.node_id,
                ),
            )[:limit]
        )

    def orphaned_symbols(self) -> tuple[str, ...]:
        self._require_edge_budget("Orphaned-symbol")
        connected = {
            identity
            for edge in self.edges
            if edge.resolved
            for identity in (edge.source_id, edge.target_id)
        }
        return tuple(
            item.symbol_id
            for item in self.symbols
            if item.symbol_id not in connected
        )

    def unresolved_edges(self) -> tuple[DependencyEdge, ...]:
        self._require_edge_budget("Unresolved-edge")
        return tuple(edge for edge in self.edges if not edge.resolved)

    def _direct(
        self,
        node_id: str,
        *,
        reverse: bool,
        kinds: Iterable[DependencyKind],
        include_unresolved: bool,
    ) -> tuple[DependencyEdge, ...]:
        allowed = _kind_filter(kinds)
        adjacency = self._reverse if reverse else self._forward
        adjacent = adjacency.get(node_id, ())
        if len(adjacent) > self.limits.maximum_edges:
            raise QueryLimitError(
                "Direct dependency query reached the edge limit."
            )
        edges = tuple(
            edge
            for edge in adjacent
            if _edge_allowed(edge, allowed, include_unresolved)
        )
        return edges

    def _traverse(
        self,
        start_ids: str | Iterable[str],
        *,
        reverse: bool,
        max_depth: int | None,
        kinds: Iterable[DependencyKind],
        include_unresolved: bool,
    ) -> TraversalResult:
        depth_limit = self._depth(max_depth)
        starts = (
            (start_ids,)
            if isinstance(start_ids, str)
            else tuple(sorted(set(start_ids)))
        )
        if len(starts) > self.limits.maximum_nodes:
            raise QueryLimitError(
                "Graph traversal start set exceeds the node limit."
            )
        allowed = _kind_filter(kinds)
        adjacency = self._reverse if reverse else self._forward
        visited = set(starts)
        queue: deque[tuple[str, int]] = deque(
            (identity, 0) for identity in starts
        )
        discovered_nodes: list[str] = []
        discovered_edges: list[DependencyEdge] = []
        maximum_depth_reached = 0
        truncated = False
        inspected_edges = 0
        while queue:
            current, depth = queue.popleft()
            maximum_depth_reached = max(maximum_depth_reached, depth)
            if depth >= depth_limit:
                continue
            for edge in adjacency.get(current, ()):
                inspected_edges += 1
                if inspected_edges > self.limits.maximum_edges:
                    truncated = True
                    queue.clear()
                    break
                if not _edge_allowed(edge, allowed, include_unresolved):
                    continue
                next_id = edge.source_id if reverse else edge.target_id
                if next_id in visited:
                    continue
                if (
                    len(discovered_nodes) >= self.limits.maximum_nodes
                    or len(discovered_edges) >= self.limits.maximum_edges
                ):
                    truncated = True
                    continue
                visited.add(next_id)
                discovered_nodes.append(next_id)
                discovered_edges.append(edge)
                queue.append((next_id, depth + 1))
        return TraversalResult(
            node_ids=tuple(discovered_nodes),
            edges=tuple(discovered_edges),
            maximum_depth_reached=maximum_depth_reached,
            truncated=truncated,
        )

    def _depth(self, requested: int | None) -> int:
        value = (
            self.limits.maximum_depth
            if requested is None
            else requested
        )
        if value < 0 or value > self.limits.maximum_depth:
            raise QueryLimitError(
                "Requested graph depth exceeds the configured limit."
            )
        return value

    def _require_edge_budget(self, operation: str) -> None:
        if len(self.edges) > self.limits.maximum_edges:
            raise QueryLimitError(
                f"{operation} query exceeds the graph edge limit."
            )


def _kind_filter(
    kinds: Iterable[DependencyKind],
) -> frozenset[DependencyKind]:
    return frozenset(DependencyKind(item) for item in kinds)


def _edge_allowed(
    edge: DependencyEdge,
    allowed: frozenset[DependencyKind],
    include_unresolved: bool,
) -> bool:
    return (
        (include_unresolved or edge.resolved)
        and (not allowed or edge.kind in allowed)
    )


def _reconstruct_path(
    source_id: str,
    target_id: str,
    parents: dict[str, tuple[str, DependencyEdge]],
) -> DependencyPath:
    nodes = [target_id]
    edges: list[DependencyEdge] = []
    current = target_id
    while current != source_id:
        parent, edge = parents[current]
        edges.append(edge)
        nodes.append(parent)
        current = parent
    nodes.reverse()
    edges.reverse()
    return DependencyPath(
        node_ids=tuple(nodes),
        edges=tuple(edges),
        confidence=min(
            (edge.confidence for edge in edges),
            default=1.0,
        ),
    )


def _adjacency(
    edges: Iterable[DependencyEdge],
    *,
    reverse: bool,
) -> dict[str, tuple[str, ...]]:
    values: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        source = edge.target_id if reverse else edge.source_id
        target = edge.source_id if reverse else edge.target_id
        values[source].add(target)
    return {
        key: tuple(sorted(targets))
        for key, targets in values.items()
    }


def _finish_order(
    nodes: tuple[str, ...],
    adjacency: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    visited: set[str] = set()
    order: list[str] = []
    for start in nodes:
        if start in visited:
            continue
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            current, expanded = stack.pop()
            if expanded:
                order.append(current)
                continue
            if current in visited:
                continue
            visited.add(current)
            stack.append((current, True))
            for target in reversed(adjacency.get(current, ())):
                if target not in visited:
                    stack.append((target, False))
    return tuple(order)


def _collect_component(
    start: str,
    adjacency: Mapping[str, tuple[str, ...]],
    assigned: set[str],
) -> set[str]:
    component: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in assigned:
            continue
        assigned.add(current)
        component.add(current)
        stack.extend(
            target
            for target in reversed(adjacency.get(current, ()))
            if target not in assigned
        )
    return component
