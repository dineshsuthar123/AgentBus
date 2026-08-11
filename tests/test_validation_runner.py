from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agentbus.validation.models import (
    CountExpectation,
    RepositorySource,
    ScenarioKind,
    ValidationReport,
    ValidationRepository,
    ValidationResourceLimits,
    ValidationScenario,
    ValidationStatus,
)
from agentbus.validation.reports import (
    render_validation_report,
    write_validation_report,
)
from agentbus.validation.runner import ValidationRunner


def _repository(root: Path) -> Path:
    root.mkdir()
    (root / "calculator.py").write_text(
        "def calculate_total(values: list[int]) -> int:\n"
        "    return sum(values)\n",
        encoding="utf-8",
    )
    (root / "test_calculator.py").write_text(
        "from calculator import calculate_total\n\n"
        "def test_total():\n"
        "    assert calculate_total([1, 2]) == 3\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'validation-calculator'\nversion = '0.1.0'\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("API_KEY=must-not-appear\n", encoding="utf-8")
    return root


def _spec(root: Path, *, maximum_files: int = 100) -> ValidationRepository:
    return ValidationRepository(
        repository_id="python-calculator",
        title="Python calculator fixture",
        source=RepositorySource.LOCAL,
        path=str(root),
        language_mix=("python",),
        expected_file_count=CountExpectation(minimum=3, maximum=3),
        expected_project_count=CountExpectation(minimum=1),
        expected_symbol_count=CountExpectation(minimum=2),
        resource_limits=ValidationResourceLimits(maximum_files=maximum_files),
        scenarios=(
            ValidationScenario(
                scenario_id="find-calculator",
                title="Find calculator implementation",
                kind=ScenarioKind.SEARCH,
                query="calculate_total",
                expected_paths=("calculator.py",),
                expected_minimum_results=1,
            ),
            ValidationScenario(
                scenario_id="context-calculator",
                title="Build implementation context",
                kind=ScenarioKind.CONTEXT,
                query="change calculate_total safely",
                expected_paths=("calculator.py",),
                expected_minimum_results=1,
            ),
        ),
    )


def test_validation_runner_indexes_and_queries_without_protected_content(tmp_path):
    root = _repository(tmp_path / "repository")

    run = ValidationRunner().run_repository(_spec(root))

    assert run.status in {
        ValidationStatus.PASS,
        ValidationStatus.PASS_WITH_WARNINGS,
    }
    assert run.file_count == 3
    assert run.project_count >= 1
    assert run.symbol_count >= 2
    assert run.languages == ("python",)
    assert all(scenario.status == ValidationStatus.PASS for scenario in run.scenarios)
    assert run.provider_calls == 0
    assert run.network_calls == 0
    serialized = run.model_dump_json()
    assert "must-not-appear" not in serialized
    assert "API_KEY" not in serialized


def test_validation_runner_fails_closed_when_repository_budget_is_exceeded(tmp_path):
    root = _repository(tmp_path / "repository")
    spec = _spec(root, maximum_files=1)

    run = ValidationRunner().run_repository(spec)

    assert run.status == ValidationStatus.FAIL
    assert any(failure.category.value == "resource" for failure in run.failures)
    assert not run.scenarios


def test_validation_report_write_is_atomic_and_payload_free(tmp_path):
    root = _repository(tmp_path / "repository")
    run = ValidationRunner().run_repository(_spec(root))
    report = ValidationReport(
        status=run.status,
        generated_at=datetime.now(UTC),
        runs=(run,),
    )

    output = write_validation_report(report, tmp_path / "report.json")
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["ok"] is True
    assert payload["network_used"] is False
    assert payload["runs"][0]["root_fingerprint"]
    assert str(root) not in output.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))
    assert "PASS" in render_validation_report(report)
