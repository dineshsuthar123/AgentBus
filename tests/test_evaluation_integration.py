import pytest

from agentbus.evaluation.runner import EvaluationRunner
from agentbus.evaluation.suites import builtin_suites


def test_core_offline_limits_do_not_depend_on_shared_runner_timing():
    suite = builtin_suites()["core-offline"]

    assert all(
        "max_elapsed_seconds" not in case.metadata["limits"]
        for case in suite.cases
    )
    parallel = next(
        case
        for case in suite.cases
        if case.case_id == "parallel-dependency-scheduling"
    )
    assert parallel.metadata["minimum_concurrency"] == 2


@pytest.mark.parametrize(
    "variant_id",
    ["single-fake", "multi-fake", "durable-sequential-fake"],
)
def test_calculator_evaluation_uses_each_runtime_workflow(tmp_path, variant_id):
    runner = EvaluationRunner(results_dir=tmp_path / variant_id)

    run = runner.run(
        "core-offline",
        variant_id=variant_id,
        case_ids={"calculator-feature"},
    )

    assert run.passed is True
    assert run.aggregate_score == 100
    assert run.case_results[0].metrics.provider.requests > 0
    assert run.case_results[0].relevant_changed_files == [
        "calculator.py",
        "test_calculator.py",
    ]


def test_durable_parallel_evaluation_covers_recovery_safety_and_integration(tmp_path):
    selected = {
        "generated-artifact-filtering",
        "durable-crash-recovery",
        "parallel-dependency-scheduling",
        "integration-conflict-safety",
        "high-risk-approval-gate",
    }
    runner = EvaluationRunner(results_dir=tmp_path / "parallel")

    run = runner.run(
        "core-offline",
        variant_id="durable-parallel-fake",
        case_ids=selected,
    )

    assert run.passed is True
    assert {item.case_id for item in run.case_results} == selected
    assert all(item.score.total == 100 for item in run.case_results)

    by_case = {item.case_id: item for item in run.case_results}
    recovery = by_case["durable-crash-recovery"]
    assert recovery.raw_metrics["attempts_per_task"]["recovery"] == 2
    assert recovery.metrics.execution.recoveries >= 1
    assert next(
        item
        for item in recovery.assertions
        if item.assertion_id == "no-successful-task-rerun"
    ).passed

    parallel = by_case["parallel-dependency-scheduling"]
    assert parallel.metrics.execution.maximum_observed_concurrency >= 2
    assert parallel.metrics.git.integration_commits == 3

    conflict = by_case["integration-conflict-safety"]
    assert conflict.run_status == "failed"
    assert conflict.metrics.git.conflict_count >= 1

    approval = by_case["high-risk-approval-gate"]
    assert approval.run_status == "waiting_for_approval"
    assert approval.reviewer_approved is None

    generated = by_case["generated-artifact-filtering"]
    assert generated.relevant_changed_files == ["module.py"]
    assert "__pycache__" not in generated.relevant_changed_files


def test_release_offline_runs_package_cli_and_report_preflight(tmp_path):
    runner = EvaluationRunner(results_dir=tmp_path / "release")

    run = runner.run(
        "release-offline",
        variant_id="durable-parallel-fake",
        case_ids={"calculator-feature"},
    )

    assert run.passed is True
    assert all(run.metadata["release_acceptance"].values())
    assert run.metadata["release_acceptance"]["storage_roundtrip"] is True
