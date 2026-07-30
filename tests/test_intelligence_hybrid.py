from __future__ import annotations

import pytest

from agentbus.intelligence import (
    ArchitectureBoundary,
    DependencyEdge,
    DependencyGraph,
    DependencyKind,
    HybridRetrievalLimits,
    HybridRetriever,
    Project,
    ProjectKind,
    QueryLimitError,
    RepositoryLexicalIndex,
    SearchQuery,
    SearchResult,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolLocation,
    edge_id,
    file_id,
    project_id,
    repository_identity,
    stable_hash,
    stable_id,
)


def _records():
    repository = repository_identity("fixtures/hybrid-search")
    owner = project_id(
        repository.repository_id,
        "services/api",
        ProjectKind.PYTHON,
        name="api",
    )
    project = Project(
        project_id=owner,
        repository_id=repository.repository_id,
        name="api",
        kind=ProjectKind.PYTHON,
        root="services/api",
        source_roots=("services/api/src",),
        test_roots=("services/api/tests",),
    )
    file_specs = (
        ("services/api/src/handler.py", False, "1"),
        ("services/api/src/model.py", False, "2"),
        ("services/api/tests/test_handler.py", True, "3"),
        ("services/api/src/unrelated.py", False, "4"),
    )
    files = tuple(
        SourceFile(
            file_id=file_id(repository.repository_id, path),
            repository_id=repository.repository_id,
            project_id=owner,
            relative_path=path,
            language=SourceLanguage.PYTHON,
            content_hash=digit * 64,
            size_bytes=100,
            parser_name="fixture",
            parser_version="1.0.0",
            test=test,
        )
        for path, test, digit in file_specs
    )
    by_path = {item.relative_path: item for item in files}

    def symbol(
        key: str,
        path: str,
        name: str,
        kind: SymbolKind = SymbolKind.FUNCTION,
        *,
        test: bool = False,
    ) -> Symbol:
        source = by_path[path]
        return Symbol(
            symbol_id=stable_id("symbol", "hybrid", key),
            file_id=source.file_id,
            project_id=owner,
            name=name,
            qualified_name=f"api.{name}",
            kind=kind,
            language=SourceLanguage.PYTHON,
            location=SymbolLocation(
                relative_path=path,
                start_line=1,
                start_column=0,
                end_line=1,
                end_column=1,
            ),
            exported=not test,
            test=test,
        )

    handler = symbol(
        "handler",
        "services/api/src/handler.py",
        "handle_request",
    )
    model = symbol(
        "model",
        "services/api/src/model.py",
        "validate_model",
    )
    test = symbol(
        "test",
        "services/api/tests/test_handler.py",
        "test_handle_request",
        SymbolKind.TEST,
        test=True,
    )
    unrelated = symbol(
        "unrelated",
        "services/api/src/unrelated.py",
        "background_cleanup",
    )
    edges = (
        DependencyEdge(
            edge_id=edge_id(
                handler.symbol_id,
                model.symbol_id,
                DependencyKind.CALLS.value,
            ),
            kind=DependencyKind.CALLS,
            source_id=handler.symbol_id,
            target_id=model.symbol_id,
            confidence=0.9,
            parser_name="fixture",
            parser_version="1.0.0",
            explanation="Handler validates the model.",
        ),
        DependencyEdge(
            edge_id=edge_id(
                test.symbol_id,
                handler.symbol_id,
                DependencyKind.TESTS.value,
            ),
            kind=DependencyKind.TESTS,
            source_id=test.symbol_id,
            target_id=handler.symbol_id,
            confidence=1.0,
            parser_name="fixture",
            parser_version="1.0.0",
            explanation="Test covers the handler.",
        ),
    )
    boundary = ArchitectureBoundary(
        boundary_id="boundary_" + stable_hash(("hybrid", "service")),
        name="API service",
        scope=("services/api/**",),
        boundary_type="service",
        source_evidence=("services/api/src/handler.py",),
        confidence=0.8,
        explanation="A service project is present.",
    )
    symbols = (handler, model, test, unrelated)
    return (project,), files, symbols, edges, (boundary,)


def _retriever(*, semantic=None, limits=None) -> HybridRetriever:
    projects, files, symbols, edges, boundaries = _records()
    lexical = RepositoryLexicalIndex(projects, files, (), symbols)
    graph = DependencyGraph(edges, symbols=symbols, files=files)
    return HybridRetriever(
        lexical,
        graph,
        files,
        symbols,
        boundaries=boundaries,
        semantic=semantic,
        limits=limits,
    )


class _SemanticFixture:
    def __init__(self, result: SearchResult | None) -> None:
        self.result = result

    def search(self, query: SearchQuery, *, stale: bool = False):
        return (self.result,) if self.result is not None else ()


def test_fuses_exact_graph_project_architecture_and_test_evidence() -> None:
    retriever = _retriever()

    results = retriever.search(
        SearchQuery(text="handle_request"),
        recent_paths=("services/api/src/model.py",),
    )
    repeated = retriever.search(
        SearchQuery(text="handle_request"),
        recent_paths=("services/api/src/model.py",),
    )
    by_name = {
        item.symbol.name: item
        for item in results
        if item.symbol is not None
    }

    assert repeated == results
    assert results[0].symbol is not None
    assert results[0].symbol.name == "handle_request"
    assert "lexical" in results[0].score_components
    assert "symbol_match" in results[0].score_components
    assert "project_proximity" in results[0].score_components
    assert "architecture" in results[0].score_components
    assert by_name["validate_model"].dependency_path
    assert by_name["validate_model"].score_components["dependency"] > 0
    assert by_name["validate_model"].score_components["recent_change"] == 4.0
    assert by_name["test_handle_request"].score_components[
        "test_relationship"
    ] == 12.0
    assert "background_cleanup" not in by_name
    assert all(
        item.explanation.startswith("Explainable hybrid")
        for item in results
    )


def test_optional_semantic_scores_are_fused_without_bypassing_snapshot() -> None:
    _, files, symbols, _, _ = _records()
    unrelated = symbols[3]
    semantic_result = SearchResult(
        rank=1,
        score=90.0,
        score_components={"semantic": 90.0},
        relative_path=unrelated.location.relative_path,
        source_hash=files[3].content_hash,
        project_id=unrelated.project_id,
        symbol=unrelated,
        explanation="Fixture semantic result.",
    )
    retriever = _retriever(
        semantic=_SemanticFixture(semantic_result)
    )

    results = retriever.search(SearchQuery(text="handle_request"))
    by_name = {
        item.symbol.name: item
        for item in results
        if item.symbol is not None
    }

    assert by_name["background_cleanup"].score_components["semantic"] == 27.0
    stale_result = semantic_result.model_copy(
        update={"source_hash": "9" * 64}
    )
    stale_retriever = _retriever(
        semantic=_SemanticFixture(stale_result)
    )
    stale_names = {
        item.symbol.name
        for item in stale_retriever.search(
            SearchQuery(text="handle_request")
        )
        if item.symbol is not None
    }
    assert "background_cleanup" not in stale_names


def test_semantic_unavailability_preserves_lexical_graph_results() -> None:
    without_semantic = _retriever().search(
        SearchQuery(text="handle_request")
    )
    unavailable = _retriever(
        semantic=_SemanticFixture(None)
    ).search(SearchQuery(text="handle_request"))

    assert unavailable == without_semantic


def test_hybrid_filters_and_stale_state_apply_to_graph_candidates() -> None:
    results = _retriever().search(
        SearchQuery(
            text="handle",
            path_prefixes=("services/api/tests",),
            symbol_kinds=(SymbolKind.TEST,),
            test_only=True,
        ),
        stale=True,
    )

    assert len(results) == 1
    assert results[0].symbol is not None
    assert results[0].symbol.kind == SymbolKind.TEST
    assert results[0].stale is True


def test_hybrid_graph_work_is_bounded() -> None:
    with pytest.raises(QueryLimitError, match="edge limit"):
        _retriever(
            limits=HybridRetrievalLimits(maximum_graph_edges=1)
        )
    with pytest.raises(QueryLimitError, match="Recent-path"):
        _retriever(
            limits=HybridRetrievalLimits(maximum_recent_paths=1)
        ).search(
            SearchQuery(text="handle_request"),
            recent_paths=(
                "services/api/src/handler.py",
                "services/api/src/handler.py",
            ),
        )
