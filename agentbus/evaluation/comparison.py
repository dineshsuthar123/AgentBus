from __future__ import annotations

from agentbus.evaluation.models import (
    ComparisonThresholds,
    EvaluationComparison,
    EvaluationRun,
    RegressionResult,
    RegressionSeverity,
)


def compare_runs(
    baseline: EvaluationRun,
    current: EvaluationRun,
    thresholds: ComparisonThresholds | None = None,
) -> EvaluationComparison:
    limits = thresholds or ComparisonThresholds()
    regressions: list[RegressionResult] = []
    baseline_cases = {item.case_id: item for item in baseline.case_results}
    current_cases = {item.case_id: item for item in current.case_results}

    for case_id, previous in baseline_cases.items():
        actual = current_cases.get(case_id)
        if actual is None:
            regressions.append(
                RegressionResult(
                    case_id=case_id,
                    metric="case_present",
                    baseline_value=True,
                    current_value=False,
                    regressed=True,
                    severity=RegressionSeverity.CRITICAL,
                    message="Previously evaluated case is missing from the current run.",
                )
            )
            continue
        if previous.passed and not actual.passed:
            regressions.append(
                RegressionResult(
                    case_id=case_id,
                    metric="case_passed",
                    baseline_value=True,
                    current_value=False,
                    regressed=True,
                    severity=RegressionSeverity.CRITICAL,
                    message="Previously passing case now fails.",
                )
            )
        if previous.verifier_passed is True and actual.verifier_passed is not True:
            regressions.append(
                RegressionResult(
                    case_id=case_id,
                    metric="verifier_passed",
                    baseline_value=True,
                    current_value=actual.verifier_passed,
                    regressed=True,
                    severity=RegressionSeverity.CRITICAL,
                    message="Verifier changed from passing to non-passing.",
                )
            )
        if actual.metrics.quality.safety_violation_count > 0:
            regressions.append(
                RegressionResult(
                    case_id=case_id,
                    metric="safety_violations",
                    baseline_value=previous.metrics.quality.safety_violation_count,
                    current_value=actual.metrics.quality.safety_violation_count,
                    regressed=True,
                    severity=RegressionSeverity.CRITICAL,
                    message="Current run contains a hard safety failure.",
                )
            )
        score_drop = previous.score.total - actual.score.total
        if score_drop > limits.score_drop:
            regressions.append(
                RegressionResult(
                    case_id=case_id,
                    metric="score",
                    baseline_value=previous.score.total,
                    current_value=actual.score.total,
                    regressed=True,
                    severity=RegressionSeverity.ERROR,
                    message=f"Score dropped by {score_drop:.2f} points.",
                )
            )
        unrelated_before = previous.metrics.quality.unrelated_file_count
        unrelated_now = actual.metrics.quality.unrelated_file_count
        if unrelated_now > unrelated_before:
            regressions.append(
                RegressionResult(
                    case_id=case_id,
                    metric="unrelated_files",
                    baseline_value=unrelated_before,
                    current_value=unrelated_now,
                    regressed=True,
                    severity=RegressionSeverity.ERROR,
                    message="Unrelated changed-file count increased.",
                )
            )
        _ratio_regression(
            regressions,
            case_id,
            "tokens",
            previous.metrics.provider.total_tokens,
            actual.metrics.provider.total_tokens,
            limits.token_increase_ratio,
            RegressionSeverity.WARNING,
        )
        _ratio_regression(
            regressions,
            case_id,
            "latency",
            previous.metrics.execution.total_duration_seconds,
            actual.metrics.execution.total_duration_seconds,
            limits.latency_increase_ratio,
            RegressionSeverity.WARNING,
        )
        retry_delta = (
            actual.metrics.execution.retries - previous.metrics.execution.retries
        )
        if retry_delta > limits.retry_increase:
            regressions.append(
                RegressionResult(
                    case_id=case_id,
                    metric="retries",
                    baseline_value=previous.metrics.execution.retries,
                    current_value=actual.metrics.execution.retries,
                    regressed=True,
                    severity=RegressionSeverity.WARNING,
                    message=f"Retries increased by {retry_delta}.",
                )
            )

    failed = [item for item in regressions if item.regressed]
    return EvaluationComparison(
        baseline_run_id=baseline.evaluation_run_id,
        current_run_id=current.evaluation_run_id,
        regressions=regressions,
        passed=not failed,
        summary=(
            "No configured regressions detected."
            if not failed
            else f"Detected {len(failed)} configured regression(s)."
        ),
    )


def _ratio_regression(
    results: list[RegressionResult],
    case_id: str,
    metric: str,
    baseline: float,
    current: float,
    threshold: float,
    severity: RegressionSeverity,
) -> None:
    if baseline <= 0:
        return
    ratio = (current - baseline) / baseline
    if ratio <= threshold:
        return
    results.append(
        RegressionResult(
            case_id=case_id,
            metric=metric,
            baseline_value=baseline,
            current_value=current,
            regressed=True,
            severity=severity,
            message=f"{metric.title()} increased by {ratio:.1%}.",
        )
    )
