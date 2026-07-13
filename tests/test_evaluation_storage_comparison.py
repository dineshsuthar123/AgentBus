import json

import pytest

from agentbus.evaluation.comparison import compare_runs
from agentbus.evaluation.errors import EvaluationStorageError
from agentbus.evaluation.models import (
    ComparisonThresholds,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRun,
    EvaluationScore,
    EvaluationVariant,
)
from agentbus.evaluation.storage import EvaluationStorage


def make_run(run_id="run-a", *, passed=True, score=100, tokens=10, seconds=1):
    metrics = EvaluationMetrics()
    metrics.quality.success = passed
    metrics.quality.safety_violation_count = 0
    metrics.provider.total_tokens = tokens
    metrics.execution.total_duration_seconds = seconds
    result = EvaluationCaseResult(
        case_id="case-a",
        title="Case A",
        passed=passed,
        run_status="succeeded" if passed else "failed",
        verifier_passed=passed,
        score=EvaluationScore(total=score),
        metrics=metrics,
    )
    return EvaluationRun(
        evaluation_run_id=run_id,
        suite_id="suite-a",
        variant=EvaluationVariant(
            variant_id="fake",
            title="Fake",
            provider="fake",
        ),
        status="completed",
        agentbus_commit_sha="abc123",
        configuration_fingerprint="fingerprint",
        case_results=[result],
        aggregate_metrics=metrics,
        aggregate_score=score,
        passed=passed,
    )


def test_storage_round_trip_export_and_sanitization(tmp_path):
    storage = EvaluationStorage(tmp_path / "results")
    run = make_run()
    run.metadata = {"api_key": "real-secret", "safe": True}

    path = storage.save_run(run)
    restored = storage.load_run(run.evaluation_run_id)
    exported = storage.export_run(run.evaluation_run_id, tmp_path / "report.json")

    assert restored.metadata["api_key"] == "[REDACTED]"
    assert "real-secret" not in path.read_text(encoding="utf-8")
    assert json.loads(exported.read_text(encoding="utf-8"))["schema_version"] == 1


def test_baseline_replacement_must_be_explicit(tmp_path):
    storage = EvaluationStorage(tmp_path / "results")
    run = make_run()
    storage.save_baseline("main", run)

    with pytest.raises(EvaluationStorageError, match="explicit"):
        storage.save_baseline("main", run)

    storage.save_baseline("main", make_run("run-b"), replace=True)
    assert storage.load_baseline("main").evaluation_run_id == "run-b"


def test_storage_rejects_unsafe_identifiers(tmp_path):
    storage = EvaluationStorage(tmp_path / "results")

    with pytest.raises(EvaluationStorageError, match="Unsafe"):
        storage.load_run("../outside")


def test_identical_run_has_no_regression():
    baseline = make_run()
    current = baseline.model_copy(update={"evaluation_run_id": "run-b"}, deep=True)

    comparison = compare_runs(baseline, current)

    assert comparison.passed is True
    assert comparison.regressions == []


def test_functional_score_and_safety_regressions_are_detected():
    baseline = make_run()
    current = make_run("run-b", passed=False, score=50)
    current.case_results[0].metrics.quality.safety_violation_count = 1

    comparison = compare_runs(baseline, current)
    metrics = {item.metric for item in comparison.regressions}

    assert comparison.passed is False
    assert {
        "case_passed",
        "verifier_passed",
        "score",
        "safety_violations",
    } <= metrics


def test_token_latency_and_retry_thresholds_are_configurable():
    baseline = make_run(tokens=100, seconds=10)
    current = make_run("run-b", tokens=120, seconds=12)
    current.case_results[0].metrics.execution.retries = 1

    lenient = compare_runs(
        baseline,
        current,
        ComparisonThresholds(
            token_increase_ratio=0.25,
            latency_increase_ratio=0.25,
            retry_increase=1,
        ),
    )
    strict = compare_runs(
        baseline,
        current,
        ComparisonThresholds(
            token_increase_ratio=0.10,
            latency_increase_ratio=0.10,
            retry_increase=0,
        ),
    )

    assert lenient.passed is True
    assert {item.metric for item in strict.regressions} == {
        "tokens",
        "latency",
        "retries",
    }


def test_missing_baseline_case_is_a_critical_regression():
    baseline = make_run()
    current = make_run("run-b")
    current.case_results = []

    comparison = compare_runs(baseline, current)

    assert comparison.passed is False
    assert comparison.regressions[0].metric == "case_present"
    assert comparison.regressions[0].severity.value == "critical"
