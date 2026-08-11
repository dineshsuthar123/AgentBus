from __future__ import annotations

from dataclasses import dataclass

import pytest

from agentbus.intelligence import (
    ArchitectureAnalyzer,
    ArchitectureInference,
    ArchitectureLimits,
    ChangeImpactAnalyzer,
    DependencyEdge,
    DependencyGraph,
    DependencyKind,
    ImpactAnalysisLimits,
    ImpactRequest,
    Project,
    ProjectKind,
    QueryLimitError,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolLocation,
    TestImpactSelector as ImpactTestSelector,
    TestSelectionLimits as SelectionLimits,
    TraversalLimits,
    content_hash,
    edge_id,
    file_id,
    project_id,
    repository_identity,
    stable_id,
)


def _edge(
    source: str,
    target: str,
    *,
    kind: DependencyKind = DependencyKind.CALLS,
    key: str = "",
) -> DependencyEdge:
    return DependencyEdge(
        edge_id=edge_id(
            source,
            target,
            kind.value,
            location_key=key,
        ),
        kind=kind,
        source_id=source,
        target_id=target,
        confidence=1.0,
        parser_name="graph-scale-fixture",
        parser_version="1.0.0",
        explanation="Controlled dependency graph scale fixture edge.",
    )


def test_long_chain_obeys_shortest_path_and_reverse_depth_budgets() -> None:
    nodes = tuple(f"chain-{index:04d}" for index in range(512))
    edges = tuple(
        _edge(nodes[index], nodes[index + 1])
        for index in range(len(nodes) - 1)
    )
    graph = DependencyGraph(
        edges,
        limits=TraversalLimits(
            maximum_depth=64,
            maximum_nodes=len(nodes),
            maximum_edges=len(edges),
        ),
    )

    path = graph.shortest_path(nodes[0], nodes[64], max_depth=64)
    beyond_budget = graph.shortest_path(
        nodes[0],
        nodes[65],
        max_depth=64,
    )
    reverse = graph.transitive_dependents(
        nodes[-1],
        max_depth=16,
    )

    assert path is not None
    assert path.node_ids == nodes[:65]
    assert len(path.edges) == 64
    assert beyond_budget is None
    assert reverse.node_ids == tuple(reversed(nodes[-17:-1]))
    assert reverse.maximum_depth_reached == 16
    assert reverse.truncated is False
    with pytest.raises(QueryLimitError, match="depth"):
        graph.shortest_path(nodes[0], nodes[65], max_depth=65)


def test_wide_reverse_traversal_is_deterministic_and_node_bounded() -> None:
    hub = "wide-hub"
    leaves = tuple(f"wide-leaf-{index:04d}" for index in range(512))
    edges = tuple(_edge(leaf, hub) for leaf in leaves)
    graph = DependencyGraph(
        edges,
        limits=TraversalLimits(
            maximum_depth=4,
            maximum_nodes=64,
            maximum_edges=len(edges),
        ),
    )

    direct = graph.dependents(hub)
    first = graph.transitive_dependents(hub, max_depth=1)
    repeated = graph.transitive_dependents(hub, max_depth=1)

    assert len(direct) == len(leaves)
    assert first == repeated
    assert len(first.node_ids) == 64
    assert len(first.edges) == 64
    assert set(first.node_ids).issubset(leaves)
    assert first.maximum_depth_reached == 1
    assert first.truncated is True


def test_disconnected_cycles_have_deterministic_component_metadata() -> None:
    edges: list[DependencyEdge] = []
    cycle_nodes: list[tuple[str, ...]] = []
    for group in range(256):
        nodes = tuple(
            f"cycle-{group:04d}-{offset}" for offset in range(3)
        )
        cycle_nodes.append(nodes)
        edges.extend(
            _edge(nodes[index], nodes[(index + 1) % len(nodes)])
            for index in range(len(nodes))
        )
    for group in range(512):
        edges.append(
            _edge(
                f"pair-{group:04d}-left",
                f"pair-{group:04d}-right",
            )
        )
    for group in range(64):
        node = f"self-loop-{group:04d}"
        edges.append(_edge(node, node))

    node_count = (256 * 3) + (512 * 2) + 64
    graph = DependencyGraph(
        edges,
        limits=TraversalLimits(
            maximum_depth=16,
            maximum_nodes=node_count,
            maximum_edges=len(edges),
        ),
    )

    components = graph.strongly_connected_components()
    cycles = graph.cycles()

    assert components == graph.strongly_connected_components()
    assert len(components) == 256 + (512 * 2) + 64
    assert len(cycles) == 256 + 64
    assert {item.node_ids for item in cycles}.issuperset(cycle_nodes)
    assert sum(len(item.edge_ids) for item in cycles) == (256 * 3) + 64


def test_dense_graph_analysis_obeys_explicit_edge_and_node_budgets() -> None:
    nodes = tuple(f"dense-{index:03d}" for index in range(48))
    edges = tuple(
        _edge(source, target)
        for source in nodes
        for target in nodes
        if source != target
    )
    limits = TraversalLimits(
        maximum_depth=4,
        maximum_nodes=len(nodes),
        maximum_edges=len(edges),
    )
    graph = DependencyGraph(edges, limits=limits)

    components = graph.strongly_connected_components()
    centrality = graph.high_centrality(limit=3)
    path = graph.shortest_path(nodes[0], nodes[-1], max_depth=1)

    assert len(components) == 1
    assert components[0].node_ids == nodes
    assert components[0].cyclic is True
    assert len(components[0].edge_ids) == len(edges)
    assert len(centrality) == 3
    assert all(item.inbound_edges == len(nodes) - 1 for item in centrality)
    assert all(item.outbound_edges == len(nodes) - 1 for item in centrality)
    assert path is not None
    assert path.node_ids == (nodes[0], nodes[-1])

    over_budget = DependencyGraph(
        edges,
        limits=TraversalLimits(
            maximum_depth=4,
            maximum_nodes=len(nodes),
            maximum_edges=len(edges) - 1,
        ),
    )
    with pytest.raises(QueryLimitError, match="edge limit"):
        over_budget.strongly_connected_components()


@dataclass(frozen=True)
class _MultiProjectGraph:
    projects: tuple[Project, ...]
    files: tuple[SourceFile, ...]
    symbols: tuple[Symbol, ...]
    graph: DependencyGraph
    source_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    source_symbol_ids: tuple[str, ...]
    test_symbol_ids: tuple[str, ...]


def _multi_project_graph(project_count: int = 8) -> _MultiProjectGraph:
    repository = repository_identity("fixtures/graph-scale/multi-project")
    projects: list[Project] = []
    files: list[SourceFile] = []
    symbols: list[Symbol] = []
    source_paths: list[str] = []
    test_paths: list[str] = []
    source_symbol_ids: list[str] = []
    test_symbol_ids: list[str] = []

    for index in range(project_count):
        root = f"services/service-{index:02d}"
        name = f"service-{index:02d}"
        owner = project_id(
            repository.repository_id,
            root,
            ProjectKind.PYTHON,
            name=name,
        )
        source_path = f"{root}/src/service.py"
        test_path = f"{root}/tests/test_service.py"
        projects.append(
            Project(
                project_id=owner,
                repository_id=repository.repository_id,
                name=name,
                kind=ProjectKind.PYTHON,
                root=root,
                source_roots=(f"{root}/src",),
                test_roots=(f"{root}/tests",),
                manifest_paths=(f"{root}/pyproject.toml",),
            )
        )
        source = SourceFile(
            file_id=file_id(repository.repository_id, source_path),
            repository_id=repository.repository_id,
            project_id=owner,
            relative_path=source_path,
            language=SourceLanguage.PYTHON,
            content_hash=content_hash(f"source:{index}"),
            size_bytes=32,
            parser_name="graph-scale-fixture",
            parser_version="1.0.0",
        )
        test = SourceFile(
            file_id=file_id(repository.repository_id, test_path),
            repository_id=repository.repository_id,
            project_id=owner,
            relative_path=test_path,
            language=SourceLanguage.PYTHON,
            content_hash=content_hash(f"test:{index}"),
            size_bytes=32,
            parser_name="graph-scale-fixture",
            parser_version="1.0.0",
            test=True,
        )
        source_symbol_id = stable_id(
            "symbol",
            "graph-scale",
            name,
            "source",
        )
        test_symbol_id = stable_id(
            "symbol",
            "graph-scale",
            name,
            "test",
        )
        symbols.extend(
            (
                Symbol(
                    symbol_id=source_symbol_id,
                    file_id=source.file_id,
                    project_id=owner,
                    name=f"run_{index}",
                    qualified_name=f"{name}.run_{index}",
                    kind=SymbolKind.FUNCTION,
                    language=SourceLanguage.PYTHON,
                    location=_location(source_path),
                    exported=True,
                ),
                Symbol(
                    symbol_id=test_symbol_id,
                    file_id=test.file_id,
                    project_id=owner,
                    name=f"test_run_{index}",
                    qualified_name=f"{name}.test_run_{index}",
                    kind=SymbolKind.TEST,
                    language=SourceLanguage.PYTHON,
                    location=_location(test_path),
                    test=True,
                ),
            )
        )
        files.extend((source, test))
        source_paths.append(source_path)
        test_paths.append(test_path)
        source_symbol_ids.append(source_symbol_id)
        test_symbol_ids.append(test_symbol_id)

    edges = [
        _edge(
            test_symbol_ids[index],
            source_symbol_ids[index],
            kind=DependencyKind.TESTS,
            key=f"test:{index}",
        )
        for index in range(project_count)
    ]
    edges.extend(
        _edge(
            source_symbol_ids[index],
            source_symbol_ids[index - 1],
            key=f"project:{index}",
        )
        for index in range(1, project_count)
    )
    graph = DependencyGraph(
        edges,
        files=files,
        symbols=symbols,
        limits=TraversalLimits(
            maximum_depth=16,
            maximum_nodes=len(files) + len(symbols),
            maximum_edges=len(edges),
        ),
    )
    return _MultiProjectGraph(
        projects=tuple(projects),
        files=tuple(files),
        symbols=tuple(symbols),
        graph=graph,
        source_paths=tuple(source_paths),
        test_paths=tuple(test_paths),
        source_symbol_ids=tuple(source_symbol_ids),
        test_symbol_ids=tuple(test_symbol_ids),
    )


def _location(path: str) -> SymbolLocation:
    return SymbolLocation(
        relative_path=path,
        start_line=1,
        start_column=0,
        end_line=1,
        end_column=1,
    )


def _architecture(fixture: _MultiProjectGraph) -> ArchitectureInference:
    return ArchitectureAnalyzer(
        limits=ArchitectureLimits(
            maximum_boundaries=64,
            maximum_evidence_paths=32,
            maximum_diagnostics=32,
            maximum_high_risk_paths=64,
        )
    ).analyze(
        fixture.projects,
        fixture.files,
        fixture.symbols,
        fixture.graph,
    )


def test_multi_project_graph_scales_architecture_impact_and_test_selection() -> None:
    fixture = _multi_project_graph()
    architecture = _architecture(fixture)
    crossings = fixture.graph.project_boundary_crossings()

    assert len(crossings) == len(fixture.projects) - 1
    assert len(architecture.project_crossing_edge_ids) == len(crossings)
    assert sum(
        boundary.boundary_type == "service"
        for boundary in architecture.boundaries
    ) == len(fixture.projects)
    assert not any(
        diagnostic.code == "architecture.graph_limit"
        for diagnostic in architecture.diagnostics
    )

    selector = ImpactTestSelector(
        fixture.graph,
        projects=fixture.projects,
        files=fixture.files,
        symbols=fixture.symbols,
        limits=SelectionLimits(
            maximum_tests=64,
            maximum_evidence=128,
            maximum_historical_fixtures=8,
            maximum_graph_edges=len(fixture.graph.edges),
        ),
    )
    analyzer = ChangeImpactAnalyzer(
        fixture.graph,
        projects=fixture.projects,
        architecture=architecture,
        test_selector=selector,
        limits=ImpactAnalysisLimits(
            maximum_graph_edges=len(fixture.graph.edges),
            maximum_hotspots=64,
        ),
    )
    result = analyzer.analyze(
        ImpactRequest(
            paths=(fixture.source_paths[0],),
            max_depth=16,
            max_nodes=64,
        )
    )

    assert result.truncated is False
    assert set(result.affected_projects) == {
        project.project_id for project in fixture.projects
    }
    assert set(result.direct_dependents) == {
        fixture.source_symbol_ids[1],
        fixture.test_symbol_ids[0],
    }
    assert set(result.architecture_crossings) == set(
        architecture.project_crossing_edge_ids
    )
    assert result.tests.selected_tests == tuple(sorted(fixture.test_paths))


def test_architecture_graph_scale_fails_closed_at_its_graph_budget() -> None:
    fixture = _multi_project_graph()
    limited_graph = DependencyGraph(
        fixture.graph.edges,
        files=fixture.files,
        symbols=fixture.symbols,
        limits=TraversalLimits(
            maximum_depth=16,
            maximum_nodes=len(fixture.graph.node_ids),
            maximum_edges=1,
        ),
    )
    result = ArchitectureAnalyzer(
        limits=ArchitectureLimits(
            maximum_boundaries=64,
            maximum_evidence_paths=32,
            maximum_diagnostics=32,
            maximum_high_risk_paths=64,
        )
    ).analyze(
        fixture.projects,
        fixture.files,
        fixture.symbols,
        limited_graph,
    )

    assert result.project_crossing_edge_ids == ()
    assert result.dependency_cycles == ()
    assert any(
        diagnostic.code == "architecture.graph_limit"
        for diagnostic in result.diagnostics
    )
