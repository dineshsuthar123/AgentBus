from __future__ import annotations

import json
import os
from pathlib import Path

from agentbus.validation.models import ValidationReport, ValidationStatus


def write_validation_report(
    report: ValidationReport,
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
        f"Repositories: {len(report.runs)}; network used: no",
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
    if report.status == ValidationStatus.PASS_WITH_WARNINGS:
        lines.append("Validation completed with bounded non-fatal warnings.")
    return "\n".join(lines)
