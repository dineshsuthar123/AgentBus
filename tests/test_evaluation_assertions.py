from pathlib import Path

from agentbus.evaluation.assertions import AssertionEvaluator, RuntimeObservation
from agentbus.evaluation.models import ContentExpectation, EvaluationCase, RunStatus


def make_case(**overrides):
    values = {
        "case_id": "assertion-case",
        "title": "Assertions",
        "task_prompt": "Make files",
        "fixture_repository_source": "fixture",
        "expected_files": ["app.py"],
        "forbidden_files": ["forbidden.py"],
        "content_expectations": [ContentExpectation(path="app.py", pattern="VALUE = 2")],
        "expected_test_command": ["python", "-m", "pytest"],
        "metadata": {
            "expected_changed_files": ["app.py"],
            "expected_relevant_files": ["app.py"],
            "limits": {
                "max_tokens": 10,
                "max_requests": 2,
                "max_elapsed_seconds": 5,
                "max_retries": 0,
            },
        },
    }
    values.update(overrides)
    return EvaluationCase(**values)


def observation(repository, **overrides):
    values = {
        "repository": repository,
        "run_status": RunStatus.SUCCEEDED.value,
        "verifier_passed": True,
        "reviewer_approved": True,
        "changed_files": ["app.py"],
        "relevant_changed_files": ["app.py"],
        "test_command": ["python", "-m", "pytest"],
        "test_exit_code": 0,
        "total_tokens": 5,
        "total_requests": 2,
        "elapsed_seconds": 1,
    }
    values.update(overrides)
    return RuntimeObservation(**values)


def test_assertions_cover_files_content_scope_review_and_budgets(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    assertions = AssertionEvaluator().evaluate(make_case(), observation(tmp_path))

    assert assertions
    assert all(item.passed for item in assertions)


def test_forbidden_file_and_secret_pattern_are_hard_failures(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "forbidden.py").write_text("unsafe\n", encoding="utf-8")

    assertions = AssertionEvaluator().evaluate(
        make_case(),
        observation(tmp_path, sanitized_diagnostic_text="api_key=real-value"),
    )
    failures = {item.assertion_id: item for item in assertions if item.passed is False}

    assert failures["forbidden-file:forbidden.py"].hard_failure is True
    assert failures["no-secret-patterns"].hard_failure is True


def test_generated_artifact_must_be_excluded_from_relevant_files(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    case = make_case(
        metadata={
            "expected_changed_files": ["app.py"],
            "expected_relevant_files": ["app.py"],
            "expected_generated_artifacts": ["__pycache__"],
        }
    )

    assertions = AssertionEvaluator().evaluate(
        case,
        observation(tmp_path, generated_artifacts=["__pycache__"]),
    )

    assert next(
        item for item in assertions if item.assertion_id == "generated-artifacts-excluded"
    ).passed


def test_task_counts_recovery_conflicts_and_source_immutability(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    case = make_case(
        metadata={
            "expected_changed_files": ["app.py"],
            "expected_relevant_files": ["app.py"],
            "expected_task_execution_counts": {"task-a": 1},
            "no_successful_task_rerun": ["task-a"],
            "expected_conflict_files": ["shared.txt"],
            "expect_source_unchanged": True,
        }
    )

    assertions = AssertionEvaluator().evaluate(
        case,
        observation(
            tmp_path,
            task_execution_counts={"task-a": 1},
            conflict_files=["shared.txt"],
            source_unchanged=True,
        ),
    )

    selected = {
        item.assertion_id: item.passed
        for item in assertions
        if item.assertion_id.startswith("task-")
        or item.assertion_id.startswith("no-successful")
        or item.assertion_id in {"conflict-files", "source-repository-unchanged"}
    }
    assert selected
    assert all(selected.values())


def test_expected_versus_actual_diagnostics_are_clear(tmp_path):
    assertions = AssertionEvaluator().evaluate(
        make_case(expected_run_status=RunStatus.FAILED),
        observation(tmp_path, run_status=RunStatus.SUCCEEDED.value),
    )
    failure = next(item for item in assertions if item.assertion_id == "run-status")

    assert failure.passed is False
    assert "expected 'failed'" in failure.message
    assert "observed 'succeeded'" in failure.message
