from datetime import timedelta

from agentbus.evaluation.models import (
    EvaluationFailureInjection,
    EvaluationSuite,
    FailureInjectionKind,
    RunStatus,
)
from agentbus.evaluation.runner import (
    ControlledLeaseClock,
    EvaluationRunner,
    OneShotCrashHook,
)
from agentbus.evaluation.suites import builtin_suites, builtin_variants


def failure_messages(run):
    result = run.case_results[0]
    return {
        "assertions": [
            f"{item.assertion_id}: {item.message}"
            for item in result.assertions
            if item.passed is False
        ],
        "failure_category": result.failure_category,
        "failure_message": result.failure_message,
        "raw_metrics": result.raw_metrics,
    }


def injected_runner(tmp_path, case):
    suite = EvaluationSuite(
        suite_id="injection-suite",
        title="Injections",
        description="Deterministic offline failure injection",
        cases=[case],
        default_variant="durable-sequential-fake",
    )
    return EvaluationRunner(
        results_dir=tmp_path / "results",
        suites={suite.suite_id: suite},
        variants=builtin_variants(),
    )


def calculator_case(kind, **updates):
    original = builtin_suites()["core-offline"].cases[0]
    values = {
        "failure_injections": [EvaluationFailureInjection(kind=kind)],
    }
    values.update(updates)
    return original.model_copy(update=values)


def test_coder_transport_failure_retries_and_recovers(tmp_path):
    case = calculator_case(FailureInjectionKind.CODER_TRANSPORT_FAILURE)

    run = injected_runner(tmp_path, case).run("injection-suite")

    assert run.passed is True, failure_messages(run)
    assert run.case_results[0].metrics.execution.retries == 1
    assert run.case_results[0].metrics.provider.requests >= 4


def test_provider_fallback_is_attributed_in_metrics(tmp_path):
    case = calculator_case(FailureInjectionKind.PROVIDER_FALLBACK)

    run = injected_runner(tmp_path, case).run("injection-suite")

    assert run.passed is True, failure_messages(run)
    assert run.case_results[0].metrics.provider.fallbacks >= 1


def test_final_reviewer_rejection_is_an_expected_evaluation_outcome(tmp_path):
    case = calculator_case(
        FailureInjectionKind.REVIEWER_REJECTION,
        expected_run_status=RunStatus.FAILED,
        expected_reviewer_approved=False,
    )

    run = injected_runner(tmp_path, case).run("injection-suite")

    assert run.passed is True
    assert run.case_results[0].run_status == "failed"
    assert run.case_results[0].reviewer_approved is False


def test_verifier_failure_blocks_completion(tmp_path):
    case = calculator_case(
        FailureInjectionKind.VERIFIER_FAILURE,
        expected_run_status=RunStatus.FAILED,
        expected_verifier_passed=False,
        expected_reviewer_approved=None,
        expected_test_command=[],
        metadata={"limits": {"max_requests": 20, "max_tokens": 500}},
    )

    run = injected_runner(tmp_path, case).run("injection-suite")

    assert run.passed is True
    assert run.case_results[0].failure_category == "verifier_failure"


def test_malformed_planner_fails_without_network_or_workspace_changes(tmp_path):
    case = calculator_case(
        FailureInjectionKind.MALFORMED_PLANNER,
        expected_run_status=RunStatus.FAILED,
        expected_verifier_passed=None,
        expected_reviewer_approved=None,
        expected_test_command=[],
        metadata={},
    )

    run = injected_runner(tmp_path, case).run("injection-suite")

    assert run.passed is True
    assert run.case_results[0].failure_category == "ModelOutputError"


def test_integration_interruption_recovers_without_quality_loss(tmp_path):
    case = calculator_case(FailureInjectionKind.DURING_INTEGRATION)

    run = injected_runner(tmp_path, case).run(
        "injection-suite", variant_id="durable-parallel-fake"
    )

    assert run.passed is True
    assert run.case_results[0].metrics.execution.recoveries >= 1


def test_lease_expiry_hook_advances_controlled_clock_without_sleep():
    case = calculator_case(FailureInjectionKind.LEASE_EXPIRY)
    clock = ControlledLeaseClock()
    hook = OneShotCrashHook(case, lease_clock=clock)
    before = clock()

    hook("after_worktree_created", "run-id", "feature")

    assert hook.fired is True
    assert clock() == before + timedelta(seconds=31)


def test_lease_expiry_is_bounded_and_recoverable(tmp_path):
    case = calculator_case(FailureInjectionKind.LEASE_EXPIRY)

    run = injected_runner(tmp_path, case).run(
        "injection-suite", variant_id="durable-parallel-fake"
    )

    assert run.passed is True, failure_messages(run)
    assert run.case_results[0].metrics.execution.recoveries >= 1
    assert run.case_results[0].metrics.execution.retries >= 1


def test_approval_rejection_is_persisted_as_a_failed_run(tmp_path):
    original = next(
        item
        for item in builtin_suites()["core-offline"].cases
        if item.case_id == "high-risk-approval-gate"
    )
    case = original.model_copy(
        update={
            "expected_run_status": RunStatus.FAILED,
            "failure_injections": [
                EvaluationFailureInjection(kind=FailureInjectionKind.APPROVAL_REJECTION)
            ],
        }
    )

    run = injected_runner(tmp_path, case).run(
        "injection-suite", variant_id="durable-parallel-fake"
    )

    assert run.passed is True, failure_messages(run)
    assert run.case_results[0].run_status == "failed"
    assert next(
        item
        for item in run.case_results[0].assertions
        if item.assertion_id == "approval-required"
    ).passed
