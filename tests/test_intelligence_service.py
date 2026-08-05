from __future__ import annotations

import json
from pathlib import Path

from agentbus.intelligence import (
    ContextRole,
    IndexOperationKind,
    IndexState,
    RepositoryIntelligenceService,
)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "sample-repository"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(
        "[project]\nname = \"sample-service\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    (workspace / "calculator.py").write_text(
        "def add(left: int, right: int) -> int:\n"
        "    \"\"\"private-source-marker\"\"\"\n"
        "    return left + right\n\n"
        "def calculate() -> int:\n"
        "    return add(1, 2)\n",
        encoding="utf-8",
    )
    (workspace / "test_calculator.py").write_text(
        "from calculator import add\n\n"
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    return workspace


def _service(tmp_path: Path) -> RepositoryIntelligenceService:
    return RepositoryIntelligenceService(
        _workspace(tmp_path),
        tmp_path / "state" / "repository-index.sqlite3",
    )


def test_service_build_update_verify_repair_gc_and_clear(tmp_path: Path) -> None:
    service = _service(tmp_path)

    built = service.build()

    assert built.operation == IndexOperationKind.BUILD
    assert built.status.state == IndexState.CURRENT
    assert built.snapshot.file_count == 2
    assert built.provider_calls == 0
    assert built.network_calls == 0
    assert str(service.workspace) not in json.dumps(
        service.repository.model_dump(mode="json")
    )

    calculator = service.workspace / "calculator.py"
    calculator.write_text(
        calculator.read_text(encoding="utf-8") + "\nVALUE = 3\n",
        encoding="utf-8",
    )
    assert service.status().state == IndexState.STALE

    updated = service.update()
    verified = service.verify()
    repaired = service.repair()
    collected = service.garbage_collect(retain=1)

    assert updated.operation == IndexOperationKind.UPDATE
    assert updated.status.state == IndexState.CURRENT
    assert verified.valid is True
    assert verified.fresh is True
    assert repaired.operation == IndexOperationKind.REPAIR
    assert repaired.status.state == IndexState.CURRENT
    assert collected.retained_snapshots == 1
    assert collected.provider_calls == 0
    assert collected.network_calls == 0

    cleared = service.clear()

    assert cleared.deleted_snapshot_count >= 1
    assert cleared.status.state == IndexState.ABSENT


def test_service_queries_are_explainable_bounded_and_source_free(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.build()

    search = service.search(
        "add",
        projects=("sample-service",),
        languages=("python",),
        limit=10,
    )
    symbols = service.symbols("calculator.py", languages=("python",))
    dependencies = service.dependencies("calculate", max_depth=2)
    dependents = service.dependencies(
        "add",
        direction="dependents",
        max_depth=2,
    )
    impact = service.impact(("calculator.py",), max_depth=3)
    tests = service.tests_for(("calculator.py",), max_depth=3)
    context = service.context_plan(
        "Change calculate to call add safely",
        role=ContextRole.CODER,
        byte_budget=20_000,
        token_budget=4_000,
        projects=("sample-service",),
    )
    overview = service.overview()

    assert search.results
    assert search.results[0].explanation
    assert any(item.name == "add" for item in symbols.symbols)
    assert dependencies.subject.name == "calculate"
    assert dependencies.edges
    assert dependents.subject.name == "add"
    assert dependents.edges
    assert impact.changed_paths == ("calculator.py",)
    assert "test_calculator.py" in tests.selected_tests
    assert context.role == ContextRole.CODER
    assert any(item.selected for item in context.candidates)
    assert overview.projects[0].name == "sample-service"
    assert overview.projects[0].file_count == 2
    assert overview.languages[0].language.value == "python"
    assert overview.symbol_kind_counts["function"] == 2
    assert overview.symbol_kind_counts["test"] >= 1
    assert overview.provider_calls == 0
    assert overview.network_calls == 0
    payload = json.dumps(
        {
            "search": search.model_dump(mode="json"),
            "symbols": symbols.model_dump(mode="json"),
            "dependencies": dependencies.model_dump(mode="json"),
            "impact": impact.model_dump(mode="json"),
            "tests": tests.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            "overview": overview.model_dump(mode="json"),
        },
        sort_keys=True,
    )
    assert "private-source-marker" not in payload
    assert '"content"' not in payload
    assert service.workspace.as_posix() not in payload
