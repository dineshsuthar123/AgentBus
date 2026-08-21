from __future__ import annotations

import json

import pytest

from agentbus.cli import main
from agentbus.product.benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkReport,
    OperationMetrics,
    write_benchmark_report,
)
from agentbus.product.performance import (
    PerformanceClassification,
    PerformanceScorecardStatus,
    compare_benchmark_reports,
    load_benchmark_report,
)


_BASELINE_TIMINGS = {
    "deterministic_run_startup": 100.0,
    "initial_index": 2_000.0,
    "incremental_index": 1_000.0,
    "lexical_search": 100.0,
    "graph_traversal": 100.0,
    "context_planning": 200.0,
    "daemon_app_startup": 1_000.0,
    "offline_replay": 100.0,
    "filesystem_tool_read": 100.0,
}


def _report(
    *,
    scale: float = 1.0,
    iterations: int = 5,
    daemon_memory: int | None = 64 * 1024 * 1024,
    storage: int = 8 * 1024 * 1024,
) -> BenchmarkReport:
    operations = tuple(
        OperationMetrics(
            name=name,
            group="synthetic",
            status="passed",
            samples_ms=(value * scale,),
            median_ms=value * scale,
            p95_ms=value * scale,
            max_ms=value * scale,
            operation_count=iterations,
            budget_ms=1_000_000.0,
            budget_passed=True,
        )
        for name, value in _BASELINE_TIMINGS.items()
    )
    environment = {
        "agentbus_version": "test",
        "python": "3.14.6",
        "implementation": "CPython",
        "system": "TestOS",
        "machine": "test-machine",
        "processor_count": 8,
        "dependencies": {"pydantic": "2.13.4"},
    }
    return BenchmarkReport(
        selected_group="all",
        iterations=iterations,
        repository_profile="small",
        repository_files=250,
        repository_bytes=50_000,
        repository_fingerprint="a" * 64,
        peak_memory_bytes=32 * 1024 * 1024,
        memory_budget_bytes=256 * 1024 * 1024,
        environment=environment,
        environment_fingerprint="b" * 64,
        operations=operations,
        generated_at="2026-08-15T00:00:00+00:00",
        daemon_peak_memory_bytes=(
            None if daemon_memory is None else int(daemon_memory * scale)
        ),
        persistent_storage_bytes=int(storage * scale),
    )


def _metric(scorecard, metric_id: str):
    return next(item for item in scorecard.metrics if item.metric_id == metric_id)


def test_ci_sized_changes_inside_broad_boundaries_are_neutral() -> None:
    scorecard = compare_benchmark_reports(_report(), _report(scale=1.5))

    assert scorecard.ok is True
    assert scorecard.status == PerformanceScorecardStatus.PASS
    assert scorecard.classification == PerformanceClassification.NEUTRAL
    assert len(scorecard.metrics) == 11
    assert {
        metric.classification for metric in scorecard.metrics
    } == {PerformanceClassification.NEUTRAL}


def test_major_performance_regressions_fail_every_release_metric() -> None:
    scorecard = compare_benchmark_reports(_report(), _report(scale=2.0))

    assert scorecard.ok is False
    assert scorecard.status == PerformanceScorecardStatus.FAIL
    assert scorecard.classification == PerformanceClassification.REGRESSION
    assert len(scorecard.regressions) == 11
    payload = scorecard.to_dict()
    assert payload["regression_count"] == 11
    assert payload["opaque_numerical_score"] is None


def test_broad_improvements_are_reported_without_an_aggregate_score() -> None:
    scorecard = compare_benchmark_reports(_report(), _report(scale=0.5))

    assert scorecard.ok is True
    assert scorecard.classification == PerformanceClassification.IMPROVEMENT
    assert all(
        metric.classification == PerformanceClassification.IMPROVEMENT
        for metric in scorecard.metrics
    )


def test_absolute_noise_floor_prevents_tiny_ratio_regression() -> None:
    baseline = _report().to_dict()
    current = _report().to_dict()
    for operation in baseline["operations"]:
        if operation["name"] == "deterministic_run_startup":
            operation["median_ms"] = 1.0
    for operation in current["operations"]:
        if operation["name"] == "deterministic_run_startup":
            operation["median_ms"] = 3.0

    scorecard = compare_benchmark_reports(baseline, current)

    deterministic = _metric(scorecard, "deterministic_run")
    assert deterministic.ratio == 3.0
    assert deterministic.classification == PerformanceClassification.NEUTRAL


def test_unavailable_measurements_remain_explicit_warnings() -> None:
    current = _report(daemon_memory=None).to_dict()
    replay = next(
        item for item in current["operations"] if item["name"] == "offline_replay"
    )
    replay["status"] = "skipped"
    replay["median_ms"] = None

    scorecard = compare_benchmark_reports(_report(), current)

    assert scorecard.ok is True
    assert scorecard.status == PerformanceScorecardStatus.PASS_WITH_WARNINGS
    assert scorecard.to_dict()["unavailable_metric_count"] == 2
    assert _metric(scorecard, "daemon_memory").classification is None
    assert _metric(scorecard, "replay").classification is None
    assert len(scorecard.warnings) == 2


def test_mismatched_repository_fixture_is_rejected() -> None:
    current = _report().to_dict()
    current["repository"]["fingerprint"] = "c" * 64

    with pytest.raises(ValueError, match="fixture does not match"):
        compare_benchmark_reports(_report(), current)


def test_baseline_loader_is_bounded_and_requires_formal_schema(tmp_path) -> None:
    valid = tmp_path / "baseline.json"
    valid.write_text(json.dumps(_report().to_dict()), encoding="utf-8")

    loaded = load_benchmark_report(valid)

    assert loaded["schema_version"] == BENCHMARK_SCHEMA_VERSION

    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"selected_group": "all"}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_benchmark_report(legacy)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="2 MiB"):
        load_benchmark_report(oversized)


def test_cli_major_regression_returns_failure_and_writes_scorecard(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    baseline_path = write_benchmark_report(_report(), tmp_path / "baseline.json")
    scorecard_path = tmp_path / "scorecard.json"
    monkeypatch.setattr(
        "agentbus.product.benchmark.run_benchmark",
        lambda *_args, **_kwargs: _report(scale=2.0),
    )

    exit_code = main(
        [
            "benchmark",
            "all",
            "--baseline",
            str(baseline_path),
            "--comparison-output",
            str(scorecard_path),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    persisted = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["benchmark_ok"] is True
    assert payload["ok"] is False
    assert payload["performance_scorecard"]["regression_count"] == 11
    assert persisted["status"] == "fail"
    assert persisted["network_used"] is False
