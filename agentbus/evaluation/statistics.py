from __future__ import annotations

import statistics
import uuid
from collections import Counter
from collections.abc import Iterable

from agentbus.evaluation.models import (
    EvaluationCaseResult,
    EvaluationRun,
    EvaluationSeries,
    RepeatedEvaluationStatistics,
    SampleStatistics,
    VariantComparisonReport,
    VariantSummary,
    utc_now,
)


def build_series(runs: list[EvaluationRun], *, series_id: str | None = None) -> EvaluationSeries:
    if not runs:
        raise ValueError("At least one evaluation run is required for a series.")
    first = runs[0]
    if any(run.suite_id != first.suite_id for run in runs):
        raise ValueError("Repeated evaluation runs must use one suite.")
    if any(run.variant.variant_id != first.variant.variant_id for run in runs):
        raise ValueError("Repeated evaluation runs must use one variant.")
    case_ids = sorted({case.case_id for run in runs for case in run.case_results})
    by_case = {
        case_id: _case_statistics(
            [case for run in runs for case in run.case_results if case.case_id == case_id]
        )
        for case_id in case_ids
    }
    return EvaluationSeries(
        series_id=series_id or uuid.uuid4().hex,
        suite_id=first.suite_id,
        variant=first.variant,
        repeat=len(runs),
        run_ids=[run.evaluation_run_id for run in runs],
        started_at=min(run.started_at for run in runs),
        completed_at=utc_now(),
        agentbus_commit_sha=first.agentbus_commit_sha,
        configuration_fingerprint=first.configuration_fingerprint,
        aggregate=_run_statistics(runs),
        by_case=by_case,
        passed=all(run.passed for run in runs),
        metadata={"individual_results_stored": True},
    )


def compare_variants(
    left_reference: str,
    left_runs: list[EvaluationRun],
    right_reference: str,
    right_runs: list[EvaluationRun],
) -> VariantComparisonReport:
    left = _variant_summary(left_reference, left_runs)
    right = _variant_summary(right_reference, right_runs)
    metrics = (
        "success_rate",
        "score_mean",
        "duration_mean_seconds",
        "tokens_mean",
        "retries_mean",
        "fallback_rate",
        "safety_failure_rate",
        "scope_violation_rate",
    )
    return VariantComparisonReport(
        left=left,
        right=right,
        differences={name: getattr(right, name) - getattr(left, name) for name in metrics},
    )


def _run_statistics(runs: list[EvaluationRun]) -> RepeatedEvaluationStatistics:
    reviewer_values = [
        case.reviewer_approved
        for run in runs
        for case in run.case_results
        if case.reviewer_approved is not None
    ]
    verifier_values = [
        case.verifier_passed
        for run in runs
        for case in run.case_results
        if case.verifier_passed is not None
    ]
    return RepeatedEvaluationStatistics(
        samples=len(runs),
        success_rate=_rate(run.passed for run in runs),
        score=_samples(run.aggregate_score for run in runs),
        duration_seconds=_samples(
            run.aggregate_metrics.execution.total_duration_seconds for run in runs
        ),
        tokens=_samples(run.aggregate_metrics.provider.total_tokens for run in runs),
        retry_distribution=_distribution(
            run.aggregate_metrics.execution.retries for run in runs
        ),
        fallback_rate=_rate(
            run.aggregate_metrics.provider.fallbacks > 0 for run in runs
        ),
        reviewer_approval_rate=_optional_rate(reviewer_values),
        verifier_pass_rate=_optional_rate(verifier_values),
        file_scope_violation_rate=_rate(
            run.aggregate_metrics.quality.unrelated_file_count > 0 for run in runs
        ),
        conflict_rate=_rate(
            run.aggregate_metrics.quality.conflict_count > 0 for run in runs
        ),
    )


def _case_statistics(cases: list[EvaluationCaseResult]) -> RepeatedEvaluationStatistics:
    reviewer_values = [case.reviewer_approved for case in cases if case.reviewer_approved is not None]
    verifier_values = [case.verifier_passed for case in cases if case.verifier_passed is not None]
    return RepeatedEvaluationStatistics(
        samples=len(cases),
        success_rate=_rate(case.passed for case in cases),
        score=_samples(case.score.total for case in cases),
        duration_seconds=_samples(
            case.metrics.execution.total_duration_seconds for case in cases
        ),
        tokens=_samples(case.metrics.provider.total_tokens for case in cases),
        retry_distribution=_distribution(case.metrics.execution.retries for case in cases),
        fallback_rate=_rate(case.metrics.provider.fallbacks > 0 for case in cases),
        reviewer_approval_rate=_optional_rate(reviewer_values),
        verifier_pass_rate=_optional_rate(verifier_values),
        file_scope_violation_rate=_rate(
            case.metrics.quality.unrelated_file_count > 0 for case in cases
        ),
        conflict_rate=_rate(case.metrics.quality.conflict_count > 0 for case in cases),
    )


def _variant_summary(reference: str, runs: list[EvaluationRun]) -> VariantSummary:
    if not runs:
        raise ValueError(f"Variant reference has no runs: {reference}")
    cases = [case for run in runs for case in run.case_results]
    return VariantSummary(
        reference=reference,
        variant_id=runs[0].variant.variant_id,
        samples=len(runs),
        success_rate=_rate(run.passed for run in runs),
        score_mean=statistics.fmean(run.aggregate_score for run in runs),
        duration_mean_seconds=statistics.fmean(
            run.aggregate_metrics.execution.total_duration_seconds for run in runs
        ),
        tokens_mean=statistics.fmean(
            run.aggregate_metrics.provider.total_tokens for run in runs
        ),
        retries_mean=statistics.fmean(
            run.aggregate_metrics.execution.retries for run in runs
        ),
        fallback_rate=_rate(
            run.aggregate_metrics.provider.fallbacks > 0 for run in runs
        ),
        safety_failure_rate=_rate(
            case.metrics.quality.safety_violation_count > 0 for case in cases
        ),
        scope_violation_rate=_rate(
            case.metrics.quality.unrelated_file_count > 0 for case in cases
        ),
    )


def _samples(values: Iterable[float | int]) -> SampleStatistics:
    sample = [float(value) for value in values]
    if not sample:
        return SampleStatistics(samples=0)
    return SampleStatistics(
        samples=len(sample),
        mean=statistics.fmean(sample),
        median=statistics.median(sample),
        minimum=min(sample),
        sample_standard_deviation=statistics.stdev(sample) if len(sample) >= 2 else 0,
    )


def _distribution(values: Iterable[int]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _rate(values: Iterable[bool]) -> float:
    sample = list(values)
    return sum(sample) / len(sample) if sample else 0


def _optional_rate(values: list[bool]) -> float | None:
    return _rate(values) if values else None
