from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentbus.validation.metrics import percentile, validation_metric
from agentbus.validation.models import (
    RepositorySource,
    ScenarioKind,
    ValidationReport,
    ValidationRepository,
    ValidationRun,
    ValidationScenario,
    ValidationStatus,
)


def test_validation_models_are_strict_and_require_stable_identities():
    with pytest.raises(ValidationError):
        ValidationRepository(
            repository_id="Unsafe ID",
            title="unsafe",
            source=RepositorySource.LOCAL,
            path=".",
        )
    with pytest.raises(ValidationError):
        ValidationRepository(
            repository_id="safe-id",
            title="unsafe extra",
            source=RepositorySource.LOCAL,
            path=".",
            invented=True,
        )


def test_scenario_shape_rejects_missing_queries_and_traversal_paths():
    with pytest.raises(ValidationError):
        ValidationScenario(
            scenario_id="search",
            title="Search",
            kind=ScenarioKind.SEARCH,
        )
    with pytest.raises(ValidationError):
        ValidationScenario(
            scenario_id="search",
            title="Search",
            kind=ScenarioKind.SEARCH,
            query="handler",
            expected_paths=("../secret",),
        )


def test_metrics_derive_bound_results_and_percentiles():
    passing = validation_metric("latency.p95", "milliseconds", 9, upper_bound=10)
    failing = validation_metric("latency.max", "milliseconds", 11, upper_bound=10)

    assert passing.passed is True
    assert failing.passed is False
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([], 0.95) is None


def test_validation_report_classification_is_transparent():
    now = datetime.now(UTC)
    run = ValidationRun(
        run_id="0123456789abcdef",
        repository_id="fixture",
        status=ValidationStatus.PASS,
        root_fingerprint="a" * 64,
        started_at=now,
        finished_at=now,
        duration_seconds=0,
        file_count=1,
        project_count=1,
        symbol_count=1,
    )
    report = ValidationReport(
        status=ValidationStatus.PASS,
        generated_at=now,
        runs=(run,),
    )

    payload = report.to_dict()
    assert payload["ok"] is True
    assert payload["repositories"] == {
        "total": 1,
        "passed": 1,
        "passed_with_warnings": 0,
        "failed": 0,
    }
    assert payload["network_used"] is False
