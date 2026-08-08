from __future__ import annotations

import pytest

from agentbus.intelligence import (
    DependencyEdge,
    DependencyGraph,
    DependencyKind,
    QueryLimitError,
    SourceLanguage,
    StrongComponent,
    Symbol,
    SymbolKind,
    SymbolLocation,
    TraversalLimits,
    edge_id,
    stable_id,
)


def _edge(
    source: str,
    target: str,
    *,
    kind: DependencyKind = DependencyKind.CALLS,
    confidence: float = 1.0,
    resolved: bool = True,
) -> DependencyEdge:
    return DependencyEdge(
        edge_id=edge_id(source, target, kind.value),
        kind=kind,
        source_id=source,
        target_id=target,
        confidence=confidence,
        parser_name="fixture",
        parser_version="1.0.0",
        explanation="Deterministic traversal fixture edge.",
        resolved=resolved,
    )


def _symbol(name: str, *, exported: bool = False) -> Symbol:
    file_identity = stable_id("file", "traversal", name)
    return Symbol(
        symbol_id=stable_id("symbol", "traversal", name),
        file_id=file_identity,
        name=name,
        qualified_name=f"fixture.{name}",
        kind=SymbolKind.FUNCTION,
        language=SourceLanguage.PYTHON,
        location=SymbolLocation(
            relative_path=f"{name}.py",
            start_line=1,
            start_column=0,
            end_line=1,
            end_column=1,
        ),
        exported=exported,
    )


def test_traverses_direct_reverse_transitive_and_shortest_paths() -> None:
    edges = (
        _edge("a", "b", confidence=0.9),
        _edge("b", "c", confidence=0.7),
        _edge("a", "external", resolved=False),
    )
    graph = DependencyGraph(edges)

    assert graph.dependencies("a") == (edges[0],)
    assert graph.dependents("b") == (edges[0],)
    assert graph.transitive_dependencies("a").node_ids == ("b", "c")
    assert graph.transitive_dependents("c").node_ids == ("b", "a")
    path = graph.shortest_path("a", "c")

    assert path is not None
    assert path.node_ids == ("a", "b", "c")
    assert path.edges == edges[:2]
    assert path.confidence == 0.7
    assert len(
        graph.dependencies("a", include_unresolved=True)
    ) == 2


def test_transitive_traversal_truncates_and_depth_is_bounded() -> None:
    graph = DependencyGraph(
        (
            _edge("a", "b"),
            _edge("b", "c"),
            _edge("c", "d"),
        ),
        limits=TraversalLimits(maximum_depth=2, maximum_nodes=1),
    )

    result = graph.transitive_dependencies("a")

    assert result.node_ids == ("b",)
    assert result.truncated is True
    with pytest.raises(QueryLimitError, match="depth"):
        graph.transitive_dependencies("a", max_depth=3)

    edge_limited = DependencyGraph(
        (
            _edge("a", "b"),
            _edge("b", "c"),
        ),
        limits=TraversalLimits(maximum_edges=1),
    )
    assert edge_limited.transitive_dependencies("a").truncated is True
    with pytest.raises(QueryLimitError, match="edge limit"):
        edge_limited.shortest_path("a", "c")


def test_detects_strong_components_and_cycles() -> None:
    graph = DependencyGraph(
        (
            _edge("a", "b"),
            _edge("b", "c"),
            _edge("c", "a"),
            _edge("d", "d"),
            _edge("e", "f"),
        )
    )

    components = graph.strongly_connected_components()
    cycles = graph.cycles()

    assert any(
        item.node_ids == ("a", "b", "c") and item.cyclic
        for item in components
    )
    assert {
        item.node_ids for item in cycles
    } == {("a", "b", "c"), ("d",)}
    assert all(isinstance(item, StrongComponent) for item in cycles)


def test_reports_project_crossings_centrality_and_unresolved_edges() -> None:
    unresolved = _edge("a", "opaque", resolved=False)
    graph = DependencyGraph(
        (
            _edge("a", "b"),
            _edge("c", "b"),
            unresolved,
        ),
        node_projects={"a": "project-a", "b": "project-b", "c": "project-b"},
    )

    crossings = graph.project_boundary_crossings()
    central = graph.high_centrality(limit=1)

    assert len(crossings) == 1
    assert crossings[0].edge.source_id == "a"
    assert central[0].node_id == "b"
    assert central[0].inbound_edges == 2
    assert graph.unresolved_edges() == (unresolved,)


def test_reports_public_and_orphaned_symbols() -> None:
    public = _symbol("public", exported=True)
    connected = _symbol("connected")
    orphan = _symbol("orphan")
    graph = DependencyGraph(
        (_edge(connected.symbol_id, public.symbol_id),),
        symbols=(orphan, public, connected),
    )

    assert graph.public_api_surfaces() == (public.symbol_id,)
    assert graph.orphaned_symbols() == (orphan.symbol_id,)
