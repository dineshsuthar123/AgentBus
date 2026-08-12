from __future__ import annotations

from pathlib import Path

from agentbus.intelligence import (
    ContextBudget,
    ContextCandidate,
    ContextPlanner,
    ContextPlanningConfig,
    ContextPlanningRequest,
    ContextRole,
    ContextSelector,
    DependencyEdge,
    DependencyGraph,
    DependencyKind,
    IndexState,
    Project,
    ProjectKind,
    RepositoryLexicalIndex,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolLocation,
    content_hash,
    edge_id,
    file_id,
    project_id,
    repository_identity,
    stable_id,
)
from agentbus.intelligence.discovery import RepositoryInventoryScanner
from agentbus.intelligence.hybrid import HybridRetriever


def test_context_budget_counts_utf8_and_selector_enforces_both_limits() -> None:
    budget = ContextBudget(byte_limit=7, token_limit=2)
    first = ContextCandidate(
        candidate_id="candidate_first",
        relative_path="first.py",
        source_hash="1" * 64,
        role=ContextRole.CODER,
        score=10.0,
        byte_count=1,
        estimated_tokens=0,
        content="four",
    )
    second = first.model_copy(
        update={
            "candidate_id": "candidate_second",
            "relative_path": "second.py",
            "source_hash": "2" * 64,
            "score": 9.0,
            "byte_count": 4,
            "estimated_tokens": 1,
            "content": "more",
        }
    )

    assert budget.measure("é").byte_count == 2
    selection = ContextSelector().select((second, first), budget)

    assert selection.selected_bytes == 4
    assert selection.selected_tokens == 1
    assert selection.candidates[0].candidate_id == "candidate_first"
    assert selection.candidates[0].selected is True
    assert selection.candidates[1].exclusion_reason == "budget_exceeded"


def _planner(tmp_path: Path):
    contents = {
        "services/api/src/handler.py": (
            "from .model import validate_model\n\n"
            "def handle_request(payload):\n"
            "    return validate_model(payload)\n"
        ),
        "services/api/src/model.py": (
            "def validate_model(payload):\n"
            "    return bool(payload)\n"
        ),
        "services/api/tests/test_handler.py": (
            "from services.api.src.handler import handle_request\n\n"
            "def test_handle_request():\n"
            "    assert handle_request({'ok': True})\n"
        ),
    }
    for relative_path, value in contents.items():
        target = tmp_path.joinpath(*relative_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8", newline="\n")
    (tmp_path / ".env").write_text(
        "REAL_KEY=must-not-appear\n",
        encoding="utf-8",
        newline="\n",
    )

    repository = repository_identity("fixtures/context-planner")
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
        ("services/api/src/handler.py", False),
        ("services/api/src/model.py", False),
        ("services/api/tests/test_handler.py", True),
    )
    files = tuple(
        SourceFile(
            file_id=file_id(repository.repository_id, path),
            repository_id=repository.repository_id,
            project_id=owner,
            relative_path=path,
            language=SourceLanguage.PYTHON,
            content_hash=content_hash(contents[path]),
            size_bytes=len(contents[path].encode("utf-8")),
            parser_name="fixture",
            parser_version="1.0.0",
            test=test,
        )
        for path, test in file_specs
    )
    by_path = {item.relative_path: item for item in files}

    def symbol(
        key: str,
        path: str,
        name: str,
        line: int,
        *,
        test: bool = False,
    ) -> Symbol:
        source = by_path[path]
        return Symbol(
            symbol_id=stable_id("symbol", "context", key),
            file_id=source.file_id,
            project_id=owner,
            name=name,
            qualified_name=f"api.{name}",
            kind=SymbolKind.TEST if test else SymbolKind.FUNCTION,
            language=SourceLanguage.PYTHON,
            location=SymbolLocation(
                relative_path=path,
                start_line=line,
                start_column=0,
                end_line=line + 1,
                end_column=1,
            ),
            exported=not test,
            test=test,
        )

    handler = symbol(
        "handler",
        "services/api/src/handler.py",
        "handle_request",
        3,
    )
    model = symbol(
        "model",
        "services/api/src/model.py",
        "validate_model",
        1,
    )
    test = symbol(
        "test",
        "services/api/tests/test_handler.py",
        "test_handle_request",
        3,
        test=True,
    )
    symbols = (handler, model, test)
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
            confidence=1.0,
            parser_name="fixture",
            parser_version="1.0.0",
            explanation="Handler validates models.",
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
            explanation="Test covers handler.",
        ),
    )
    lexical = RepositoryLexicalIndex((project,), files, (), symbols)
    graph = DependencyGraph(edges, files=files, symbols=symbols)
    retriever = HybridRetriever(lexical, graph, files, symbols)
    inventory = RepositoryInventoryScanner(tmp_path).scan()
    planner = ContextPlanner(inventory, retriever, files, symbols)
    return planner, files, symbols


def test_context_planner_is_deterministic_attributed_and_dependency_aware(
    tmp_path: Path,
) -> None:
    planner, _, _ = _planner(tmp_path)
    request = ContextPlanningRequest(
        task="Update `handle_request` validation",
        role=ContextRole.CODER,
        byte_budget=10_000,
        token_budget=2_500,
        changed_paths=("services/api/src/model.py",),
    )

    first = planner.plan(request)
    second = planner.plan(request)
    selected = tuple(
        item for item in first.candidates if item.selected
    )

    assert first == second
    assert first.selected_bytes <= first.byte_budget
    assert first.selected_tokens <= first.token_budget
    assert selected
    assert any(
        item.relative_path == "services/api/src/model.py"
        and "changed_file" in item.reasons
        for item in selected
    )
    assert any("dependency_neighbor" in item.reasons for item in selected)
    assert all(item.source_hash for item in selected)
    assert ".env" not in repr(first)
    assert "must-not-appear" not in repr(first)


def test_context_planner_tailors_scores_by_role(tmp_path: Path) -> None:
    planner, _, symbols = _planner(tmp_path)
    coder = planner.plan(
        ContextPlanningRequest(
            task="handle request test",
            role=ContextRole.CODER,
            byte_budget=10_000,
            token_budget=2_500,
        )
    )
    verifier = planner.plan(
        ContextPlanningRequest(
            task="handle request test",
            role=ContextRole.VERIFIER,
            byte_budget=10_000,
            token_budget=2_500,
        )
    )
    test_id = symbols[2].symbol_id
    coder_test = next(
        item for item in coder.candidates if item.symbol_id == test_id
    )
    verifier_test = next(
        item for item in verifier.candidates if item.symbol_id == test_id
    )

    assert verifier_test.score > coder_test.score
    assert all(item.role == ContextRole.CODER for item in coder.candidates)
    assert all(
        item.role == ContextRole.VERIFIER for item in verifier.candidates
    )
    assert planner._queries("Überprüfung ändern", ()) == (
        "Überprüfung",
        "ändern",
    )
    signaled = planner.plan(
        ContextPlanningRequest(
            task="Investigate the failure",
            role=ContextRole.VERIFIER,
            byte_budget=10_000,
            token_budget=2_500,
            tool_results=("Failure in test_handle_request",),
        )
    )
    assert any(
        item.symbol_id == test_id for item in signaled.candidates
    )


def test_exact_task_match_survives_a_tight_candidate_cap(
    tmp_path: Path,
) -> None:
    planner, _, symbols = _planner(tmp_path)
    limited = ContextPlanner(
        planner.inventory,
        planner.retriever,
        planner.files,
        planner.symbols,
        config=ContextPlanningConfig(maximum_candidates=1),
    )

    plan = limited.plan(
        ContextPlanningRequest(
            task="`validate_model` review",
            role=ContextRole.CODER,
            byte_budget=10_000,
            token_budget=2_500,
            changed_paths=(
                "services/api/src/handler.py",
                "services/api/src/model.py",
            ),
        )
    )

    assert len(plan.candidates) == 1
    assert plan.candidates[0].symbol_id == symbols[1].symbol_id
    assert plan.candidates[0].selected is True
    assert "symbol_match" in plan.candidates[0].reasons
    assert "task_match" in plan.candidates[0].reasons


def test_context_planner_excludes_hash_mismatches_and_warns(
    tmp_path: Path,
) -> None:
    planner, _, _ = _planner(tmp_path)
    model = tmp_path / "services" / "api" / "src" / "model.py"
    original = model.read_text(encoding="utf-8")
    changed = original.replace("bool", "list")
    assert len(changed.encode("utf-8")) == len(original.encode("utf-8"))
    model.write_text(changed, encoding="utf-8")

    plan = planner.plan(
        ContextPlanningRequest(
            task="validate_model",
            role=ContextRole.REVIEWER,
            byte_budget=10_000,
            token_budget=2_500,
            index_state=IndexState.STALE,
            changed_paths=(
                "services/api/src/model.py",
                "services/api/src/new_file.py",
            ),
        )
    )
    mismatched = tuple(
        item
        for item in plan.candidates
        if item.relative_path == "services/api/src/model.py"
    )

    assert mismatched
    assert all(item.selected is False for item in mismatched)
    assert all(
        item.exclusion_reason == "source_hash_mismatch"
        for item in mismatched
    )
    assert plan.stale_warning is not None
    assert "mismatched content was excluded" in plan.stale_warning
    assert "not represented" in plan.stale_warning
    assert changed not in repr(plan)
