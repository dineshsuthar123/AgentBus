from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentbus.product.soak import ResourceTrend, SoakCycleResult, SoakReport
from agentbus.validation.models import (
    ScenarioKind,
    ValidationReport,
    ValidationRun,
    ValidationScenarioResult,
    ValidationStatus,
)
from agentbus.validation.reliability import run_reliability_validation
from agentbus.validation.reports import (
    render_reliability_scorecard,
    write_validation_report,
)


def _validation_run(repository_id: str) -> ValidationRun:
    now = datetime.now(UTC)
    return ValidationRun(
        run_id=f"run-{repository_id}-0123456789abcdef",
        repository_id=repository_id,
        status=ValidationStatus.PASS,
        root_fingerprint="a" * 64,
        started_at=now,
        finished_at=now,
        duration_seconds=0.01,
        file_count=3,
        project_count=1,
        symbol_count=2,
        scenarios=(
            ValidationScenarioResult(
                scenario_id="index-integrity",
                kind=ScenarioKind.INDEX,
                status=ValidationStatus.PASS,
                duration_seconds=0.002,
                observed_count=1,
            ),
        ),
    )


def _corpus_report(**_kwargs) -> ValidationReport:
    return ValidationReport(
        status=ValidationStatus.PASS,
        generated_at=datetime.now(UTC),
        runs=(_validation_run("generated-fixture"),),
    )


def _resource_trends(*, memory_available: bool = True) -> tuple[ResourceTrend, ...]:
    names = (
        ("process_count", "processes"),
        ("owned_worktree_count", "worktrees"),
        ("state_database_bytes", "bytes"),
        ("index_database_bytes", "bytes"),
        ("trace_bytes", "bytes"),
        ("memory_bytes", "bytes"),
        ("handle_count", "handles"),
        ("thread_count", "threads"),
    )
    return tuple(
        ResourceTrend(
            name=name,
            unit=unit,
            before=None if name == "memory_bytes" and not memory_available else 0,
            peak=None if name == "memory_bytes" and not memory_available else 10,
            after=None if name == "memory_bytes" and not memory_available else 0,
            scope=f"test {name}",
        )
        for name, unit in names
    )


def _soak_report(*, memory_available: bool = True) -> SoakReport:
    cycles = (
        SoakCycleResult(
            cycle=0,
            run_id="soak-0",
            status="succeeded",
            event_count=3,
            managed_tool_calls=3,
            approval_count=1,
            mcp_call_count=1,
            lease_released=True,
            replayed=True,
            trace_written=True,
            indexed=True,
            daemon_reconnected=True,
            worktree_cleaned=True,
            cleanup_failure_count=0,
            duration_seconds=0.01,
        ),
        SoakCycleResult(
            cycle=1,
            run_id="soak-1",
            status="cancelled",
            event_count=3,
            managed_tool_calls=3,
            approval_count=1,
            mcp_call_count=1,
            lease_released=True,
            replayed=True,
            trace_written=True,
            indexed=True,
            daemon_reconnected=True,
            worktree_cleaned=True,
            cleanup_failure_count=0,
            duration_seconds=0.02,
        ),
    )
    return SoakReport(
        profile="quick",
        requested_runs=2,
        completed_runs=2,
        parallelism=1,
        seed=2026,
        duration_limit_seconds=30,
        duration_seconds=0.03,
        stopped_by_duration=False,
        repository_files=4,
        repository_fingerprint="b" * 64,
        successful_runs=1,
        intentional_cancellations=1,
        failed_runs=0,
        tool_calls=6,
        approval_count=2,
        mcp_call_count=2,
        replay_count=2,
        trace_write_count=2,
        index_update_count=2,
        daemon_reconnect_count=2,
        daemon_restart_count=2,
        worktree_cleanup_count=2,
        failed_cleanup_count=0,
        event_count=6,
        event_gap_count=0,
        stale_lease_count=0,
        leaked_worktree_count=0,
        leaked_process_count=0,
        state_database_integrity=True,
        index_database_integrity=True,
        sqlite_bytes_before=0,
        sqlite_bytes_after=1_024,
        memory_growth_bytes=0,
        peak_memory_bytes=10 if memory_available else 0,
        memory_budget_bytes=1_024,
        resource_trends=_resource_trends(memory_available=memory_available),
        cycles=cycles,
    )


class _LocalValidationRunner:
    def run_repository(self, repository):
        return _validation_run(repository.repository_id)


def test_reliability_scorecard_is_explicit_offline_and_path_free(tmp_path: Path):
    local_repository = tmp_path / "Customer Repository"
    local_repository.mkdir()

    scorecard = run_reliability_validation(
        repository_paths=(local_repository,),
        soak_runner=lambda **_kwargs: _soak_report(),
        corpus_runner=_corpus_report,
        validation_runner=_LocalValidationRunner(),
    )

    payload = scorecard.to_dict()
    assert scorecard.classification == ValidationStatus.PASS
    assert payload["status"] == "PASS"
    assert payload["ok"] is True
    assert payload["offline"] is True
    assert payload["network_used"] is False
    assert payload["scenarios_run"] == 4
    assert payload["repositories"] == {"fixtures": 2, "real_local": 1}
    assert payload["process_leaks"]["count"] == 0
    assert payload["worktree_leaks"]["count"] == 0
    assert payload["db_integrity"]["passed"] is True
    assert payload["index_integrity"]["passed"] is True
    assert payload["replay_integrity"]["passed"] == 2
    assert payload["cancellation_results"]["passed"] == 1
    assert payload["restart_results"]["passed"] == 2
    assert payload["latency"]["samples"] == 4
    assert payload["memory"]["available"] is True
    assert "score" not in json.dumps(payload).lower()
    assert str(local_repository) not in json.dumps(payload)

    rendered = render_reliability_scorecard(scorecard)
    for label in (
        "process_leaks=0",
        "worktree_leaks=0",
        "db_integrity=PASS",
        "index_integrity=PASS",
        "replay=2/2",
        "cancellation=1/1",
        "restart=2/2",
        "latency_samples=4",
        "memory_peak=10",
    ):
        assert label in rendered

    output = write_validation_report(scorecard, tmp_path / "scorecard.json")
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "PASS"
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_integrity_is_visible_and_classifies_scorecard_as_failed():
    failed_soak = replace(_soak_report(), index_database_integrity=False)

    scorecard = run_reliability_validation(
        soak_runner=lambda **_kwargs: failed_soak,
        corpus_runner=_corpus_report,
    )

    assert scorecard.classification == ValidationStatus.FAIL
    assert scorecard.ok is False
    assert scorecard.index_integrity.status == ValidationStatus.FAIL
    assert any(
        failure.category.value == "indexing" for failure in scorecard.failures
    )


def test_aggregate_failed_run_count_cannot_be_hidden_by_cycle_records():
    inconsistent_soak = replace(_soak_report(), failed_runs=1)

    scorecard = run_reliability_validation(
        soak_runner=lambda **_kwargs: inconsistent_soak,
        corpus_runner=_corpus_report,
    )

    assert scorecard.classification == ValidationStatus.FAIL
    assert any(
        "lifecycle run failure" in failure.summary
        for failure in scorecard.failures
    )


def test_unavailable_memory_is_reported_not_estimated():
    scorecard = run_reliability_validation(
        soak_runner=lambda **_kwargs: _soak_report(memory_available=False),
        corpus_runner=_corpus_report,
    )

    assert scorecard.classification == ValidationStatus.PASS_WITH_WARNINGS
    assert scorecard.memory.available is False
    assert scorecard.memory.peak_bytes is None
    assert scorecard.memory.growth_bytes is None
    assert scorecard.memory.within_budget is None


def test_reliability_rejects_duplicate_or_unbounded_local_paths(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(ValueError, match="unique"):
        run_reliability_validation(repository_paths=(repository, repository))
    with pytest.raises(ValueError, match="at most 32"):
        run_reliability_validation(repository_paths=(repository,) * 33)
