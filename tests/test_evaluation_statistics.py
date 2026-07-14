import json

from agentbus import eval as eval_cli
from agentbus.evaluation.models import (
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRun,
    EvaluationScore,
    EvaluationVariant,
)
from agentbus.evaluation.statistics import build_series, compare_variants
from agentbus.evaluation.storage import EvaluationStorage


def _run(
    run_id: str,
    *,
    score: float,
    passed: bool = True,
    seconds: float = 1,
    tokens: int = 10,
    retries: int = 0,
    fallbacks: int = 0,
    approved: bool = True,
    verified: bool = True,
    unrelated: int = 0,
    conflicts: int = 0,
) -> EvaluationRun:
    metrics = EvaluationMetrics()
    metrics.quality.success = passed
    metrics.quality.reviewer_approved = approved
    metrics.quality.verifier_passed = verified
    metrics.quality.unrelated_file_count = unrelated
    metrics.quality.conflict_count = conflicts
    metrics.execution.total_duration_seconds = seconds
    metrics.execution.retries = retries
    metrics.provider.total_tokens = tokens
    metrics.provider.fallbacks = fallbacks
    case = EvaluationCaseResult(
        case_id="case-a",
        title="Case A",
        passed=passed,
        run_status="succeeded" if passed else "failed",
        reviewer_approved=approved,
        verifier_passed=verified,
        score=EvaluationScore(total=score),
        metrics=metrics.model_copy(deep=True),
    )
    return EvaluationRun(
        evaluation_run_id=run_id,
        suite_id="suite-a",
        variant=EvaluationVariant(
            variant_id="durable-fake",
            title="Durable fake",
            provider="fake",
            durable=True,
        ),
        status="completed",
        agentbus_commit_sha="abc",
        configuration_fingerprint="fingerprint",
        case_results=[case],
        aggregate_metrics=metrics,
        aggregate_score=score,
        passed=passed,
    )


def test_repeated_statistics_use_sample_standard_deviation_and_rates():
    runs = [
        _run("one", score=80, seconds=1, tokens=10, retries=0),
        _run("two", score=90, seconds=2, tokens=20, retries=1, fallbacks=1),
        _run(
            "three",
            score=100,
            passed=False,
            seconds=3,
            tokens=30,
            retries=1,
            approved=False,
            verified=False,
            unrelated=1,
            conflicts=1,
        ),
    ]

    series = build_series(runs, series_id="series-a")
    stats = series.aggregate

    assert stats.samples == 3
    assert stats.success_rate == 2 / 3
    assert stats.score.mean == 90
    assert stats.score.median == 90
    assert stats.score.minimum == 80
    assert stats.score.sample_standard_deviation == 10
    assert stats.duration_seconds.median == 2
    assert stats.tokens.mean == 20
    assert stats.retry_distribution == {"0": 1, "1": 2}
    assert stats.fallback_rate == 1 / 3
    assert stats.reviewer_approval_rate == 2 / 3
    assert stats.verifier_pass_rate == 2 / 3
    assert stats.file_scope_violation_rate == 1 / 3
    assert stats.conflict_rate == 1 / 3
    assert series.by_case["case-a"].samples == 3
    assert "statistical significance" in stats.interpretation_note


def test_series_storage_keeps_individual_runs_and_aggregate(tmp_path):
    storage = EvaluationStorage(tmp_path / "results")
    runs = [_run("one", score=100), _run("two", score=100)]
    for run in runs:
        storage.save_run(run)
    series = build_series(runs, series_id="series-a")
    storage.save_series(series)

    restored = storage.load_series("series-a")
    samples = storage.runs_for_reference("series-a")
    exported = storage.export_series("series-a", tmp_path / "series.json")

    assert restored.run_ids == ["one", "two"]
    assert [run.evaluation_run_id for run in samples] == ["one", "two"]
    assert json.loads(exported.read_text(encoding="utf-8"))["aggregate"]["samples"] == 2


def test_variant_comparison_is_neutral_and_reports_required_metrics():
    left = [_run("left", score=80, seconds=2, tokens=20, retries=1)]
    right = [_run("right", score=90, seconds=3, tokens=30, retries=0)]

    report = compare_variants("left", left, "right", right)

    assert report.differences["score_mean"] == 10
    assert report.differences["duration_mean_seconds"] == 1
    assert report.differences["tokens_mean"] == 10
    assert report.differences["retries_mean"] == -1
    assert "does not declare a best" in report.interpretation_note.lower()


def test_compare_variants_cli_exports_markdown(tmp_path, capsys):
    storage = EvaluationStorage(tmp_path / "results")
    storage.save_run(_run("left", score=80))
    storage.save_run(_run("right", score=90))
    output = tmp_path / "comparison.md"

    assert (
        eval_cli.main(
            [
                "--results-dir",
                str(storage.root),
                "compare-variants",
                "left",
                "right",
                "--format",
                "markdown",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    rendered = output.read_text(encoding="utf-8")
    assert "Success rate" in rendered
    assert "Difference (right-left)" in rendered
    assert "declare a best variant" in rendered
    assert "AgentBus variant comparison" in capsys.readouterr().out


def test_run_repeat_dispatches_to_repeated_runner(tmp_path, monkeypatch, capsys):
    runs = [_run("one", score=100), _run("two", score=100)]
    series = build_series(runs, series_id="series-a")
    calls = []

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        def run_repeated(self, suite_id, *, repeat, **kwargs):
            calls.append((suite_id, repeat, kwargs))
            return series

    monkeypatch.setattr(eval_cli, "EvaluationRunner", FakeRunner)
    assert (
        eval_cli.main(
            [
                "--results-dir",
                str(tmp_path / "results"),
                "run",
                "--suite",
                "core-offline",
                "--variant",
                "durable-parallel-fake",
                "--repeat",
                "2",
            ]
        )
        == 0
    )
    assert calls[0][0:2] == ("core-offline", 2)
    assert "Evaluation series: series-a" in capsys.readouterr().out
