from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from agentbus.product.soak import SoakReport, run_soak
from agentbus.security.redaction import redact_text
from agentbus.validation.corpus import run_validation_corpus
from agentbus.validation.metrics import percentile
from agentbus.validation.models import (
    FailureCategory,
    ReliabilityFailure,
    ReliabilityIntegrityEvidence,
    ReliabilityLatencyEvidence,
    ReliabilityLeakEvidence,
    ReliabilityMemoryEvidence,
    ReliabilityOperationEvidence,
    ReliabilityRepositoryEvidence,
    ReliabilityScenarioEvidence,
    ReliabilityScorecard,
    RepositoryScale,
    RepositorySource,
    ScenarioKind,
    ValidationReport,
    ValidationRepository,
    ValidationRun,
    ValidationScenario,
    ValidationStatus,
)
from agentbus.validation.runner import ValidationRunner


_MAXIMUM_LOCAL_REPOSITORIES = 32
_IDENTIFIER_CHARACTER = re.compile(r"[^a-z0-9._-]+")
SoakRunner = Callable[..., SoakReport]
CorpusRunner = Callable[..., ValidationReport]


def run_reliability_validation(
    *,
    repository_paths: Sequence[str | Path] = (),
    duration_seconds: float | None = None,
    runs: int | None = None,
    parallelism: int | None = None,
    repository_files: int | None = None,
    seed: int = 2026,
    soak_runner: SoakRunner | None = None,
    corpus_runner: CorpusRunner | None = None,
    validation_runner: ValidationRunner | None = None,
) -> ReliabilityScorecard:
    """Build an offline scorecard from explicit, inspectable reliability evidence."""

    _validate_options(
        duration_seconds=duration_seconds,
        runs=runs,
        parallelism=parallelism,
        repository_files=repository_files,
        seed=seed,
    )
    local_roots = _resolve_local_roots(repository_paths)
    started_at = datetime.now(UTC)
    started = time.monotonic()
    selected_validation_runner = validation_runner or ValidationRunner()
    failures: list[ReliabilityFailure] = []
    warnings: list[str] = []
    scenario_results: list[ReliabilityScenarioEvidence] = []
    fixture_evidence: list[ReliabilityRepositoryEvidence] = []
    local_evidence: list[ReliabilityRepositoryEvidence] = []
    latency_samples: list[float] = []

    try:
        corpus_report = (corpus_runner or run_validation_corpus)(
            offline=True,
        )
    except Exception as exc:
        failures.append(
            _safe_failure(
                FailureCategory.REPOSITORY,
                "Generated repository fixture validation could not complete.",
                exc,
            )
        )
    else:
        _collect_repository_report(
            corpus_report,
            source="fixture",
            evidence=fixture_evidence,
            scenarios=scenario_results,
            failures=failures,
            warnings=warnings,
            latency_samples=latency_samples,
        )

    identifiers: set[str] = set()
    for position, root in enumerate(local_roots, start=1):
        repository_id = _local_repository_id(root, position, identifiers)
        specification = ValidationRepository(
            repository_id=repository_id,
            title=f"Explicit local repository {repository_id}",
            source=RepositorySource.LOCAL,
            path=str(root),
            scale=RepositoryScale.REAL_WORLD,
            known_characteristics=("explicit local checkout",),
            scenarios=(
                ValidationScenario(
                    scenario_id="index-integrity",
                    title="Verify the local repository index",
                    kind=ScenarioKind.INDEX,
                    expected_minimum_results=1,
                ),
            ),
        )
        run = selected_validation_runner.run_repository(specification)
        _collect_repository_run(
            run,
            source="real_local",
            evidence=local_evidence,
            scenarios=scenario_results,
            failures=failures,
            warnings=warnings,
            latency_samples=latency_samples,
        )

    soak_report: SoakReport | None = None
    try:
        soak_report = (soak_runner or run_soak)(
            profile="quick",
            duration_seconds=duration_seconds,
            runs=runs,
            parallelism=parallelism,
            seed=seed,
            repository_files=repository_files,
        )
    except Exception as exc:
        failures.append(
            _safe_failure(
                FailureCategory.PROCESS,
                "The bounded lifecycle reliability run could not complete.",
                exc,
            )
        )

    if soak_report is None:
        process_leaks = _unavailable_leak("AgentBus-owned child processes")
        worktree_leaks = _unavailable_leak("AgentBus-owned Git worktrees")
        db_integrity = _unavailable_integrity(
            "Durable state database integrity was not checked."
        )
        index_integrity = _unavailable_integrity(
            "Repository index integrity was not checked."
        )
        replay_integrity = _operation_result(
            attempted=0,
            passed=0,
            detail="Offline replay was not exercised.",
        )
        cancellation_results = _operation_result(
            attempted=0,
            passed=0,
            detail="Intentional cancellation was not exercised.",
        )
        restart_results = _operation_result(
            attempted=0,
            passed=0,
            detail="Durable restart recovery was not exercised.",
        )
        memory = ReliabilityMemoryEvidence(
            status=ValidationStatus.PASS_WITH_WARNINGS,
            available=False,
        )
    else:
        _collect_soak_report(
            soak_report,
            evidence=fixture_evidence,
            scenarios=scenario_results,
            failures=failures,
            warnings=warnings,
            latency_samples=latency_samples,
        )
        process_leaks = _leak_result(
            soak_report.leaked_process_count,
            "AgentBus-owned child processes",
        )
        worktree_leaks = _leak_result(
            soak_report.leaked_worktree_count,
            "AgentBus-owned Git worktrees",
        )
        db_integrity = _integrity_result(
            soak_report.state_database_integrity,
            "SQLite quick-check and foreign-key validation for durable state.",
        )
        index_integrity = _integrity_result(
            soak_report.index_database_integrity,
            "Current index schema, SQLite quick-check, and foreign keys.",
        )
        replay_integrity = _operation_result(
            attempted=soak_report.completed_runs,
            passed=min(soak_report.replay_count, soak_report.completed_runs),
            detail="Deterministic offline trace replays completed.",
        )
        cancellation_attempts = sum(cycle.cycle % 2 == 1 for cycle in soak_report.cycles)
        cancellation_results = _operation_result(
            attempted=cancellation_attempts,
            passed=sum(cycle.status == "cancelled" for cycle in soak_report.cycles),
            detail="Intentional durable cancellations reached cancelled state.",
        )
        restart_results = _operation_result(
            attempted=soak_report.completed_runs,
            passed=min(
                soak_report.daemon_restart_count,
                soak_report.completed_runs,
            ),
            detail="Persisted daemon event cursors recovered after restart.",
        )
        memory = _memory_result(soak_report)
        _collect_aggregate_failures(
            soak_report,
            process_leaks=process_leaks,
            worktree_leaks=worktree_leaks,
            db_integrity=db_integrity,
            index_integrity=index_integrity,
            replay_integrity=replay_integrity,
            cancellation_results=cancellation_results,
            restart_results=restart_results,
            memory=memory,
            failures=failures,
        )

    if cancellation_results.status == ValidationStatus.PASS_WITH_WARNINGS:
        warnings.append(
            "No intentional cancellation cycle ran; use at least two lifecycle runs."
        )
    if memory.status == ValidationStatus.PASS_WITH_WARNINGS:
        warnings.append("Python allocation memory was not measurable on this run.")

    latency = _latency_evidence(latency_samples)
    warnings = list(dict.fromkeys(warnings))
    failures = _deduplicate_failures(failures)
    classification = (
        ValidationStatus.FAIL
        if failures
        else ValidationStatus.PASS_WITH_WARNINGS
        if warnings
        else ValidationStatus.PASS
    )
    return ReliabilityScorecard(
        classification=classification,
        generated_at=started_at,
        duration_seconds=time.monotonic() - started,
        scenarios_run=len(scenario_results),
        scenario_results=tuple(scenario_results),
        repository_fixtures=tuple(fixture_evidence),
        real_local_repositories=tuple(local_evidence),
        failures=tuple(failures),
        process_leaks=process_leaks,
        worktree_leaks=worktree_leaks,
        db_integrity=db_integrity,
        index_integrity=index_integrity,
        replay_integrity=replay_integrity,
        cancellation_results=cancellation_results,
        restart_results=restart_results,
        latency=latency,
        memory=memory,
        warnings=tuple(warnings),
    )


def _collect_repository_report(
    report: ValidationReport,
    *,
    source: str,
    evidence: list[ReliabilityRepositoryEvidence],
    scenarios: list[ReliabilityScenarioEvidence],
    failures: list[ReliabilityFailure],
    warnings: list[str],
    latency_samples: list[float],
) -> None:
    for run in report.runs:
        _collect_repository_run(
            run,
            source=source,
            evidence=evidence,
            scenarios=scenarios,
            failures=failures,
            warnings=warnings,
            latency_samples=latency_samples,
        )
    warnings.extend(report.warnings)
    if report.status == ValidationStatus.FAIL and not any(
        run.failures for run in report.runs
    ):
        failures.append(
            ReliabilityFailure(
                category=FailureCategory.REPOSITORY,
                summary="Repository fixture validation failed during setup.",
            )
        )


def _collect_repository_run(
    run: ValidationRun,
    *,
    source: str,
    evidence: list[ReliabilityRepositoryEvidence],
    scenarios: list[ReliabilityScenarioEvidence],
    failures: list[ReliabilityFailure],
    warnings: list[str],
    latency_samples: list[float],
) -> None:
    evidence.append(
        ReliabilityRepositoryEvidence(
            repository_id=run.repository_id,
            source=source,
            status=run.status,
            scenarios_run=len(run.scenarios),
            file_count=run.file_count,
            project_count=run.project_count,
            symbol_count=run.symbol_count,
            duration_seconds=run.duration_seconds,
            warning_count=len(run.warnings),
            failure_count=len(run.failures),
        )
    )
    warnings.extend(
        f"{run.repository_id}: {warning}" for warning in run.warnings
    )
    for scenario in run.scenarios:
        scenarios.append(
            ReliabilityScenarioEvidence(
                scenario_id=scenario.scenario_id,
                source="repository",
                status=scenario.status,
                repository_id=run.repository_id,
                duration_seconds=scenario.duration_seconds,
                detail=scenario.detail,
            )
        )
        latency_samples.append(scenario.duration_seconds)
    for failure in run.failures:
        failures.append(
            ReliabilityFailure(
                category=failure.category,
                summary=failure.summary,
                detail=failure.detail,
                repository_id=failure.repository_id,
                scenario_id=failure.scenario_id,
            )
        )


def _collect_soak_report(
    report: SoakReport,
    *,
    evidence: list[ReliabilityRepositoryEvidence],
    scenarios: list[ReliabilityScenarioEvidence],
    failures: list[ReliabilityFailure],
    warnings: list[str],
    latency_samples: list[float],
) -> None:
    fixture_status = (
        ValidationStatus.FAIL
        if report.failed_runs or not report.index_database_integrity
        else ValidationStatus.PASS_WITH_WARNINGS
        if report.stopped_by_duration
        else ValidationStatus.PASS
    )
    evidence.append(
        ReliabilityRepositoryEvidence(
            repository_id="soak-generated-repository",
            source="fixture",
            status=fixture_status,
            scenarios_run=report.completed_runs,
            file_count=report.repository_files,
            project_count=0,
            symbol_count=0,
            duration_seconds=report.duration_seconds,
            warning_count=int(report.stopped_by_duration),
            failure_count=report.failed_runs,
        )
    )
    if report.stopped_by_duration:
        warnings.append(
            "The lifecycle duration limit stopped additional requested cycles."
        )
    for cycle in report.cycles:
        passed = cycle.status in {"succeeded", "cancelled"} and cycle.error is None
        status = ValidationStatus.PASS if passed else ValidationStatus.FAIL
        scenarios.append(
            ReliabilityScenarioEvidence(
                scenario_id=f"lifecycle-{cycle.cycle:05d}",
                source="soak",
                status=status,
                repository_id="soak-generated-repository",
                duration_seconds=cycle.duration_seconds,
                detail=cycle.error,
            )
        )
        latency_samples.append(cycle.duration_seconds)
        if not passed:
            failures.append(
                ReliabilityFailure(
                    category=FailureCategory.PROCESS,
                    summary=f"Lifecycle scenario {cycle.cycle} failed.",
                    detail=cycle.error,
                    repository_id="soak-generated-repository",
                    scenario_id=f"lifecycle-{cycle.cycle:05d}",
                )
            )


def _collect_aggregate_failures(
    report: SoakReport,
    *,
    process_leaks: ReliabilityLeakEvidence,
    worktree_leaks: ReliabilityLeakEvidence,
    db_integrity: ReliabilityIntegrityEvidence,
    index_integrity: ReliabilityIntegrityEvidence,
    replay_integrity: ReliabilityOperationEvidence,
    cancellation_results: ReliabilityOperationEvidence,
    restart_results: ReliabilityOperationEvidence,
    memory: ReliabilityMemoryEvidence,
    failures: list[ReliabilityFailure],
) -> None:
    checks = (
        (
            process_leaks.status,
            FailureCategory.PROCESS,
            f"Detected {process_leaks.count or 0} AgentBus-owned process leak(s).",
        ),
        (
            worktree_leaks.status,
            FailureCategory.REPOSITORY,
            f"Detected {worktree_leaks.count or 0} AgentBus-owned worktree leak(s).",
        ),
        (
            db_integrity.status,
            FailureCategory.STORAGE,
            "Durable state database integrity validation failed.",
        ),
        (
            index_integrity.status,
            FailureCategory.INDEXING,
            "Repository index integrity validation failed.",
        ),
        (
            replay_integrity.status,
            FailureCategory.REPLAY,
            "One or more offline replay attempts failed.",
        ),
        (
            cancellation_results.status,
            FailureCategory.CANCELLATION,
            "One or more intentional cancellation attempts failed.",
        ),
        (
            restart_results.status,
            FailureCategory.STORAGE,
            "One or more durable restart recovery attempts failed.",
        ),
        (
            memory.status,
            FailureCategory.RESOURCE,
            "Measured Python allocation growth exceeded its bounded budget.",
        ),
    )
    for status, category, summary in checks:
        if status == ValidationStatus.FAIL:
            failures.append(ReliabilityFailure(category=category, summary=summary))
    extras = (
        (report.failed_runs, FailureCategory.PROCESS, "lifecycle run failure"),
        (report.failed_cleanup_count, FailureCategory.RESOURCE, "cleanup failure"),
        (report.event_gap_count, FailureCategory.STORAGE, "durable event gap"),
        (report.stale_lease_count, FailureCategory.STORAGE, "stale lease"),
    )
    for count, category, label in extras:
        if count:
            failures.append(
                ReliabilityFailure(
                    category=category,
                    summary=f"Detected {count} {label}(s) after lifecycle validation.",
                )
            )
    if report.completed_runs == 0:
        failures.append(
            ReliabilityFailure(
                category=FailureCategory.PROCESS,
                summary="No bounded lifecycle scenario completed.",
            )
        )


def _leak_result(count: int, scope: str) -> ReliabilityLeakEvidence:
    return ReliabilityLeakEvidence(
        status=ValidationStatus.PASS if count == 0 else ValidationStatus.FAIL,
        checked=True,
        count=count,
        scope=scope,
    )


def _unavailable_leak(scope: str) -> ReliabilityLeakEvidence:
    return ReliabilityLeakEvidence(
        status=ValidationStatus.FAIL,
        checked=False,
        count=None,
        scope=scope,
    )


def _integrity_result(passed: bool, detail: str) -> ReliabilityIntegrityEvidence:
    return ReliabilityIntegrityEvidence(
        status=ValidationStatus.PASS if passed else ValidationStatus.FAIL,
        checked=True,
        passed=passed,
        detail=detail,
    )


def _unavailable_integrity(detail: str) -> ReliabilityIntegrityEvidence:
    return ReliabilityIntegrityEvidence(
        status=ValidationStatus.FAIL,
        checked=False,
        passed=None,
        detail=detail,
    )


def _operation_result(
    *,
    attempted: int,
    passed: int,
    detail: str,
) -> ReliabilityOperationEvidence:
    failed = attempted - passed
    status = (
        ValidationStatus.FAIL
        if failed
        else ValidationStatus.PASS_WITH_WARNINGS
        if attempted == 0
        else ValidationStatus.PASS
    )
    return ReliabilityOperationEvidence(
        status=status,
        attempted=attempted,
        passed=passed,
        failed=failed,
        detail=detail,
    )


def _memory_result(report: SoakReport) -> ReliabilityMemoryEvidence:
    trend = next(
        (item for item in report.resource_trends if item.name == "memory_bytes"),
        None,
    )
    if trend is None or not trend.measurable:
        return ReliabilityMemoryEvidence(
            status=ValidationStatus.PASS_WITH_WARNINGS,
            available=False,
        )
    within_budget = report.memory_growth_bytes <= report.memory_budget_bytes
    return ReliabilityMemoryEvidence(
        status=ValidationStatus.PASS if within_budget else ValidationStatus.FAIL,
        available=True,
        peak_bytes=report.peak_memory_bytes,
        growth_bytes=report.memory_growth_bytes,
        budget_bytes=report.memory_budget_bytes,
        within_budget=within_budget,
    )


def _latency_evidence(samples: Sequence[float]) -> ReliabilityLatencyEvidence:
    if not samples:
        return ReliabilityLatencyEvidence(samples=0, total_seconds=0)
    total = sum(samples)
    return ReliabilityLatencyEvidence(
        samples=len(samples),
        total_seconds=total,
        mean_milliseconds=(total / len(samples)) * 1_000,
        p95_milliseconds=(percentile(samples, 0.95) or 0) * 1_000,
        maximum_milliseconds=max(samples) * 1_000,
    )


def _resolve_local_roots(paths: Sequence[str | Path]) -> tuple[Path, ...]:
    if len(paths) > _MAXIMUM_LOCAL_REPOSITORIES:
        raise ValueError(
            f"Reliability validation accepts at most {_MAXIMUM_LOCAL_REPOSITORIES} "
            "explicit local repositories."
        )
    roots: list[Path] = []
    identities: set[str] = set()
    for value in paths:
        root = Path(value).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Reliability repository paths must be directories.")
        identity = os.path.normcase(str(root))
        if identity in identities:
            raise ValueError("Reliability repository paths must be unique.")
        identities.add(identity)
        roots.append(root)
    return tuple(roots)


def _local_repository_id(
    root: Path,
    position: int,
    used: set[str],
) -> str:
    candidate = _IDENTIFIER_CHARACTER.sub("-", root.name.strip().lower()).strip(
        "._-"
    )
    base = (candidate or "local-repository")[:68].rstrip("._-")
    identifier = base
    if identifier in used:
        identifier = f"{base}-{position}"
    used.add(identifier)
    return identifier


def _safe_failure(
    category: FailureCategory,
    summary: str,
    error: BaseException,
) -> ReliabilityFailure:
    detail = redact_text(str(error), max_chars=2_048).replace("\x00", "\\0")
    return ReliabilityFailure(
        category=category,
        summary=summary,
        detail=detail or type(error).__name__,
    )


def _deduplicate_failures(
    failures: Sequence[ReliabilityFailure],
) -> list[ReliabilityFailure]:
    unique: list[ReliabilityFailure] = []
    seen: set[tuple[object, ...]] = set()
    for failure in failures:
        identity = (
            failure.category,
            failure.summary,
            failure.repository_id,
            failure.scenario_id,
        )
        if identity not in seen:
            seen.add(identity)
            unique.append(failure)
    return unique


def _validate_options(
    *,
    duration_seconds: float | None,
    runs: int | None,
    parallelism: int | None,
    repository_files: int | None,
    seed: int,
) -> None:
    if duration_seconds is not None and duration_seconds <= 0:
        raise ValueError("Reliability duration must be greater than zero.")
    if runs is not None and runs <= 0:
        raise ValueError("Reliability runs must be greater than zero.")
    if parallelism is not None and parallelism <= 0:
        raise ValueError("Reliability parallelism must be greater than zero.")
    if repository_files is not None and repository_files <= 0:
        raise ValueError("Reliability repository-files must be greater than zero.")
    if seed < 0:
        raise ValueError("Reliability seed must be non-negative.")
