from __future__ import annotations

from pathlib import Path

from agentbus.intelligence import (
    DependencyKind,
    IndexStore,
    ReferenceResolver,
    RepositoryIndexer,
    repository_identity,
    workspace_identity,
)
from agentbus.intelligence.parsers import ParserRegistry, PythonAstParser


def _snapshot(tmp_path: Path) -> tuple[IndexStore, str]:
    repository = repository_identity("fixtures/reference-resolution")
    workspace = workspace_identity(repository.repository_id, [""])
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")
    result = RepositoryIndexer(
        tmp_path,
        repository,
        workspace,
        store,
        registry=ParserRegistry((PythonAstParser(),)),
    ).build()
    return store, result.snapshot.snapshot_id


def test_resolves_imported_simple_names_with_bounded_confidence(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def helper():\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "test_service.py").write_text(
        "from service import helper\n\n"
        "def test_helper():\n"
        "    assert helper()\n",
        encoding="utf-8",
    )
    store, snapshot_id = _snapshot(tmp_path)
    symbols = store.list_symbols(snapshot_id)
    helper = next(
        item for item in symbols if item.qualified_name == "service.helper"
    )
    call = next(
        item
        for item in store.list_references(snapshot_id)
        if (
            item.kind == DependencyKind.CALLS
            and item.location.relative_path == "test_service.py"
        )
    )

    assert call.target_symbol_id == helper.symbol_id
    assert call.unresolved_target is None
    assert call.confidence <= 0.8
    assert "explicitly imported" in call.explanation
    assert any(
        edge.kind == DependencyKind.TESTS
        and edge.target_id == helper.symbol_id
        for edge in store.list_edges(snapshot_id)
    )


def test_ambiguous_simple_names_remain_unresolved(
    tmp_path: Path,
) -> None:
    for name in ("first.py", "second.py"):
        (tmp_path / name).write_text(
            "def helper():\n    return True\n",
            encoding="utf-8",
        )
    (tmp_path / "consumer.py").write_text(
        "def consume():\n    return helper()\n",
        encoding="utf-8",
    )
    store, snapshot_id = _snapshot(tmp_path)

    call = next(
        item
        for item in store.list_references(snapshot_id)
        if (
            item.kind == DependencyKind.CALLS
            and item.location.relative_path == "consumer.py"
        )
    )

    assert call.target_symbol_id is None
    assert call.unresolved_target == "helper"


def test_unique_repository_name_is_an_explicit_low_confidence_heuristic(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def helper():\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        "def consume():\n    return helper()\n",
        encoding="utf-8",
    )
    store, snapshot_id = _snapshot(tmp_path)

    call = next(
        item
        for item in store.list_references(snapshot_id)
        if (
            item.kind == DependencyKind.CALLS
            and item.location.relative_path == "consumer.py"
        )
    )

    assert call.target_symbol_id is not None
    assert call.confidence <= 0.6
    assert "heuristically" in call.explanation


def test_unique_name_in_another_language_remains_unresolved(
    tmp_path: Path,
) -> None:
    (tmp_path / "api.ts").write_text(
        "export interface Calculation { left: number; }\n",
        encoding="utf-8",
    )
    (tmp_path / "calculator.py").write_text(
        "def calculate(left: int) -> int:\n"
        "    return left\n",
        encoding="utf-8",
    )
    repository = repository_identity("fixtures/cross-language-resolution")
    workspace = workspace_identity(repository.repository_id, [""])
    store = IndexStore(tmp_path / ".agentbus-test" / "cross-language.sqlite3")
    result = RepositoryIndexer(
        tmp_path,
        repository,
        workspace,
        store,
    ).build()
    symbols = store.list_symbols(result.snapshot.snapshot_id)
    typescript_left = next(
        item
        for item in symbols
        if item.name == "left" and item.location.relative_path == "api.ts"
    )
    python_references = [
        item
        for item in store.list_references(result.snapshot.snapshot_id)
        if (
            item.location.relative_path == "calculator.py"
            and item.unresolved_target == "left"
        )
    ]

    assert python_references
    assert all(
        item.target_symbol_id != typescript_left.symbol_id
        for item in python_references
    )


def test_resolver_results_are_deterministic_for_reordered_records(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def helper():\n"
        "    return True\n\n"
        "def caller():\n"
        "    return helper()\n",
        encoding="utf-8",
    )
    store, snapshot_id = _snapshot(tmp_path)
    symbols = store.list_symbols(snapshot_id)
    modules = store.list_modules(snapshot_id)
    references = store.list_references(snapshot_id)

    first = ReferenceResolver(symbols, modules).rebind(references)
    second = ReferenceResolver(
        reversed(symbols),
        reversed(modules),
    ).rebind(reversed(references))

    assert first == second


def test_resolved_references_are_stable_across_unchanged_builds(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def helper():\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        "from service import helper\n\n"
        "def consume():\n"
        "    return helper()\n",
        encoding="utf-8",
    )
    repository = repository_identity("fixtures/resolution-idempotency")
    workspace = workspace_identity(repository.repository_id, [""])
    store = IndexStore(tmp_path / ".agentbus-test" / "stable.sqlite3")
    indexer = RepositoryIndexer(
        tmp_path,
        repository,
        workspace,
        store,
        registry=ParserRegistry((PythonAstParser(),)),
    )

    first = indexer.build()
    first_references = store.list_references(first.snapshot.snapshot_id)
    second = indexer.build()
    second_references = store.list_references(second.snapshot.snapshot_id)

    assert second.unchanged is True
    assert second.snapshot.snapshot_id == first.snapshot.snapshot_id
    assert second_references == first_references
