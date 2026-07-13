import json

import pytest

from agentbus import eval as eval_cli
from agentbus.evaluation.models import (
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRun,
    EvaluationScore,
    EvaluationVariant,
)
from agentbus.evaluation.storage import EvaluationStorage


def make_run(run_id="run-a", *, passed=True, score=100):
    metrics = EvaluationMetrics()
    metrics.quality.success = passed
    result = EvaluationCaseResult(
        case_id="calculator-feature",
        title="Calculator",
        passed=passed,
        run_status="succeeded" if passed else "failed",
        score=EvaluationScore(total=score),
        metrics=metrics,
    )
    return EvaluationRun(
        evaluation_run_id=run_id,
        suite_id="core-offline",
        variant=EvaluationVariant(
            variant_id="durable-parallel-fake",
            title="Fake",
            provider="fake",
            durable=True,
            parallel=True,
            max_workers=2,
        ),
        status="completed",
        agentbus_commit_sha="abc123",
        configuration_fingerprint="fingerprint",
        case_results=[result],
        aggregate_metrics=metrics,
        aggregate_score=score,
        passed=passed,
    )


def invoke(tmp_path, *arguments):
    return eval_cli.main(["--results-dir", str(tmp_path / "results"), *arguments])


def test_list_supports_human_and_json_output(tmp_path, capsys):
    assert invoke(tmp_path, "list") == 0
    assert "core-offline" in capsys.readouterr().out

    assert invoke(tmp_path, "list", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert {item["suite_id"] for item in payload["suites"]} == {
        "core-offline",
        "azure-smoke",
    }


def test_show_does_not_prompt_and_returns_run_status(tmp_path, monkeypatch, capsys):
    storage = EvaluationStorage(tmp_path / "results")
    storage.save_run(make_run())
    monkeypatch.setattr(
        "builtins.input",
        lambda *args, **kwargs: pytest.fail("diagnostic command prompted for input"),
    )

    assert invoke(tmp_path, "show", "run-a") == 0
    assert "Evaluation run: run-a" in capsys.readouterr().out

    storage.save_run(make_run("run-failed", passed=False, score=0))
    assert invoke(tmp_path, "show", "run-failed") == 1


def test_compare_returns_nonzero_for_regression(tmp_path, capsys):
    storage = EvaluationStorage(tmp_path / "results")
    storage.save_run(make_run())
    storage.save_run(make_run("run-b", passed=False, score=0))

    assert invoke(tmp_path, "compare", "run-a", "run-b") == 1
    assert "Regression result: FAIL" in capsys.readouterr().out


def test_baseline_save_compare_and_explicit_replace(tmp_path, capsys):
    storage = EvaluationStorage(tmp_path / "results")
    storage.save_run(make_run())

    assert invoke(tmp_path, "baseline", "save", "run-a", "--name", "main") == 0
    assert invoke(tmp_path, "baseline", "save", "run-a", "--name", "main") == 2
    assert "explicit" in capsys.readouterr().out
    assert (
        invoke(
            tmp_path,
            "baseline",
            "save",
            "run-a",
            "--name",
            "main",
            "--replace",
        )
        == 0
    )
    assert invoke(tmp_path, "baseline", "compare", "run-a", "--name", "main") == 0


def test_export_writes_sanitized_machine_readable_report(tmp_path):
    storage = EvaluationStorage(tmp_path / "results")
    run = make_run()
    run.metadata = {"api_key": "must-not-export"}
    storage.save_run(run)
    output = tmp_path / "export.json"

    assert invoke(tmp_path, "export", "run-a", "--output", str(output)) == 0
    payload = output.read_text(encoding="utf-8")
    assert "must-not-export" not in payload
    assert json.loads(payload)["evaluation_run_id"] == "run-a"


def test_run_forwards_filters_and_returns_nonzero_on_failed_assertions(
    tmp_path, monkeypatch
):
    calls = []

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        def run(self, suite_id, **kwargs):
            calls.append((suite_id, kwargs))
            return make_run(passed=False, score=0)

    monkeypatch.setattr(eval_cli, "EvaluationRunner", FakeRunner)

    exit_code = invoke(
        tmp_path,
        "run",
        "--suite",
        "core-offline",
        "--variant",
        "single-fake",
        "--case",
        "calculator-feature",
        "--tag",
        "core",
        "--fail-fast",
        "--preserve-fixtures",
    )

    assert exit_code == 1
    assert calls[0][0] == "core-offline"
    assert calls[0][1]["case_ids"] == {"calculator-feature"}
    assert calls[0][1]["tags"] == {"core"}
    assert calls[0][1]["fail_fast"] is True
    assert calls[0][1]["preserve_fixtures"] is True
    assert calls[0][1]["live"] is False


def test_live_variant_requires_explicit_consent_without_calling_provider(
    tmp_path, monkeypatch, capsys
):
    class SentinelRunner:
        def __init__(self, **kwargs):
            pass

        def run(self, *args, **kwargs):
            if not kwargs["live"]:
                raise eval_cli.EvaluationError(
                    "live variant requires explicit --live consent"
                )
            return make_run()

    monkeypatch.setattr(eval_cli, "EvaluationRunner", SentinelRunner)

    assert (
        invoke(
            tmp_path,
            "run",
            "--suite",
            "azure-smoke",
            "--variant",
            "durable-azure",
        )
        == 2
    )
    assert "explicit --live" in capsys.readouterr().out


def test_live_flag_requires_explicit_suite_before_runner_creation(tmp_path, capsys):
    assert invoke(tmp_path, "run", "--live") == 2
    assert "explicit --suite" in capsys.readouterr().out


def test_live_warning_displays_provider_and_hard_budgets(tmp_path, monkeypatch, capsys):
    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        def run(self, *args, **kwargs):
            assert kwargs["live"] is True
            assert kwargs["max_requests"] == 3
            assert kwargs["max_tokens"] == 900
            return make_run()

    monkeypatch.setattr(eval_cli, "EvaluationRunner", FakeRunner)

    assert (
        invoke(
            tmp_path,
            "run",
            "--suite",
            "azure-smoke",
            "--variant",
            "durable-azure",
            "--live",
            "--max-requests",
            "3",
            "--max-tokens",
            "900",
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "WARNING" in output
    assert "Provider: azure" in output
    assert "Hard request budget: 3" in output
    assert "Hard token budget: 900" in output


def test_explicit_zero_budget_is_not_silently_replaced(tmp_path, monkeypatch):
    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        def run(self, *args, **kwargs):
            assert kwargs["max_requests"] == 0
            raise ValueError("evaluation budgets must be positive")

    monkeypatch.setattr(eval_cli, "EvaluationRunner", FakeRunner)

    assert invoke(tmp_path, "run", "--max-requests", "0") == 2
