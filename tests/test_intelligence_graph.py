from __future__ import annotations

from pathlib import Path

import pytest

from agentbus.intelligence import (
    DependencyGraphBuilder,
    DependencyGraph,
    DependencyKind,
    GraphBuildLimits,
    IndexingResult,
    IndexStore,
    QueryLimitError,
    RepositoryIndexer,
    graph_fingerprint,
    repository_identity,
    workspace_identity,
)
from agentbus.intelligence.parsers import ParserRegistry, PythonAstParser


def _index(tmp_path: Path) -> tuple[IndexStore, IndexingResult]:
    repository = repository_identity("fixtures/dependency-graph")
    workspace = workspace_identity(repository.repository_id, [""])
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")
    result = RepositoryIndexer(
        tmp_path,
        repository,
        workspace,
        store,
        registry=ParserRegistry((PythonAstParser(),)),
    ).build()
    return store, result


def test_indexer_persists_typed_dependency_edges(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def helper():\n"
        "    return True\n\n"
        "def caller():\n"
        "    return helper()\n",
        encoding="utf-8",
    )
    store, result = _index(tmp_path)

    edges = store.list_edges(result.snapshot.snapshot_id)
    graph = DependencyGraph.from_store(
        store,
        result.snapshot.snapshot_id,
    )

    assert edges
    assert graph.edges == edges
    assert result.snapshot.edge_count == len(edges)
    assert result.snapshot.graph_hash == graph_fingerprint(edges)
    call_edges = [
        edge for edge in edges if edge.kind == DependencyKind.CALLS
    ]
    assert call_edges
    assert all(edge.resolved for edge in call_edges)
    assert all(edge.confidence > 0.49 for edge in call_edges)
    assert all(edge.parser_name == "python-ast" for edge in edges)
    assert all(edge.parser_version for edge in edges)
    assert all(edge.location is not None for edge in edges)
    assert all(edge.explanation for edge in edges)


def test_graph_keeps_unresolved_references_opaque_and_uncertain(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def caller():\n    return missing_dependency()\n",
        encoding="utf-8",
    )
    store, result = _index(tmp_path)

    unresolved = [
        edge
        for edge in store.list_edges(result.snapshot.snapshot_id)
        if not edge.resolved
    ]

    assert unresolved
    assert all(edge.target_id.startswith("unresolved_") for edge in unresolved)
    assert all(edge.confidence <= 0.49 for edge in unresolved)
    assert all(
        "missing_dependency" not in edge.target_id for edge in unresolved
    )


def test_graph_adds_explicit_test_relationships(
    tmp_path: Path,
) -> None:
    (tmp_path / "test_service.py").write_text(
        "def helper():\n"
        "    return True\n\n"
        "def test_helper():\n"
        "    assert helper()\n",
        encoding="utf-8",
    )
    store, result = _index(tmp_path)

    edges = store.list_edges(result.snapshot.snapshot_id)

    assert any(edge.kind == DependencyKind.TESTS for edge in edges)


def test_graph_build_is_deterministic_and_bounded(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def helper():\n"
        "    return True\n\n"
        "def caller():\n"
        "    helper()\n"
        "    helper()\n",
        encoding="utf-8",
    )
    store, result = _index(tmp_path)
    files = store.list_files(result.snapshot.snapshot_id)
    symbols = store.list_symbols(result.snapshot.snapshot_id)
    references = store.list_references(result.snapshot.snapshot_id)

    first = DependencyGraphBuilder().build(files, symbols, references)
    second = DependencyGraphBuilder().build(
        reversed(files),
        reversed(symbols),
        reversed(references),
    )

    assert first == second
    with pytest.raises(QueryLimitError, match="graph build limit"):
        DependencyGraphBuilder(
            limits=GraphBuildLimits(maximum_references=1)
        ).build(files, symbols, references)
