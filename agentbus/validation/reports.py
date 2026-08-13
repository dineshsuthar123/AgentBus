from __future__ import annotations

import json
import os
from pathlib import Path

from agentbus.validation.models import (
    ReliabilityScorecard,
    ValidationReport,
    ValidationStatus,
)


def write_validation_report(
    report: ValidationReport | ReliabilityScorecard,
    output: str | Path,
) -> Path:
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.is_symlink():
        raise ValueError("validation report output cannot be a symlink")
    temporary = destination.with_name(f".{destination.name}.tmp")
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def render_validation_report(report: ValidationReport) -> str:
    lines = [
        f"AgentBus repository validation: {report.status.value}",
        f"Repositories: {len(report.runs)}; "
        f"network used: {'yes' if report.network_used else 'no'}",
    ]
    for run in report.runs:
        lines.append(
            f"  [{run.status.value}] {run.repository_id}: "
            f"files={run.file_count} projects={run.project_count} "
            f"symbols={run.symbol_count} duration={run.duration_seconds:.3f}s"
        )
        for failure in run.failures:
            lines.append(f"    {failure.category.value}: {failure.summary}")
        for warning in run.warnings:
            lines.append(f"    warning: {warning}")
    for warning in report.warnings:
        lines.append(f"  warning: {warning}")
    if report.status == ValidationStatus.PASS_WITH_WARNINGS:
        lines.append("Validation completed with bounded non-fatal warnings.")
    return "\n".join(lines)


def render_reliability_scorecard(scorecard: ReliabilityScorecard) -> str:
    memory = scorecard.memory
    latency = scorecard.latency
    lines = [
        f"AgentBus reliability validation: {scorecard.classification.value}",
        f"Scenarios: {scorecard.scenarios_run}; "
        f"fixtures={len(scorecard.repository_fixtures)} "
        f"real_local={len(scorecard.real_local_repositories)}; network used: no",
        f"  process_leaks={scorecard.process_leaks.count} "
        f"worktree_leaks={scorecard.worktree_leaks.count}",
        f"  db_integrity={scorecard.db_integrity.status.value} "
        f"index_integrity={scorecard.index_integrity.status.value}",
        f"  replay={scorecard.replay_integrity.passed}/"
        f"{scorecard.replay_integrity.attempted} "
        f"cancellation={scorecard.cancellation_results.passed}/"
        f"{scorecard.cancellation_results.attempted} "
        f"restart={scorecard.restart_results.passed}/"
        f"{scorecard.restart_results.attempted}",
        f"  latency_samples={latency.samples} "
        f"p95_ms={_optional_number(latency.p95_milliseconds)} "
        f"max_ms={_optional_number(latency.maximum_milliseconds)}",
        (
            f"  memory_peak={memory.peak_bytes} growth={memory.growth_bytes} "
            f"budget={memory.budget_bytes} bytes"
            if memory.available
            else "  memory=not measurable"
        ),
    ]
    for repository in (
        *scorecard.repository_fixtures,
        *scorecard.real_local_repositories,
    ):
        lines.append(
            f"  [{repository.status.value}] {repository.source}:"
            f"{repository.repository_id} files={repository.file_count} "
            f"scenarios={repository.scenarios_run}"
        )
    for failure in scorecard.failures:
        lines.append(f"  failure [{failure.category.value}]: {failure.summary}")
    for warning in scorecard.warnings:
        lines.append(f"  warning: {warning}")
    return "\n".join(lines)


def _optional_number(value: float | None) -> str:
    return "not measured" if value is None else f"{value:.3f}"
