from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import agentbus.product.soak as soak_module
from agentbus.cli import main
from agentbus.product.soak import run_soak, soak_profile


def test_release_candidate_profile_is_bounded_for_short_ci_soak():
    profile = soak_profile("release-candidate")

    assert 5 * 60 <= profile.duration_seconds <= 10 * 60
    assert profile.runs == 10_000
    assert profile.parallelism == 2
    assert profile.repository_files == 100

    with pytest.raises(ValueError, match="Unsupported soak profile"):
        soak_profile("unknown")


def test_resource_trends_do_not_rescan_growing_collections_per_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scans = {"trace": 0, "worktrees": 0}

    def trace_size(_root: Path) -> int:
        scans["trace"] += 1
        return 128

    def active_worktrees(_manager: object) -> int:
        scans["worktrees"] += 1
        return 0

    monkeypatch.setattr(soak_module, "_directory_bytes", trace_size)
    monkeypatch.setattr(
        soak_module,
        "_active_worktree_count",
        active_worktrees,
    )
    tracker = soak_module._ResourceTracker(
        state_database=tmp_path / "state.db",
        index_database=tmp_path / "index.db",
        trace_root=tmp_path / "trace",
        worktree_manager=object(),
    )

    for _ in range(1_000):
        tracker.sample()
    tracker.finish()

    assert scans == {"trace": 2, "worktrees": 2}


def test_soak_integrity_checks_fail_closed_without_creating_databases(tmp_path):
    missing_state = tmp_path / "missing-state.db"
    missing_index = tmp_path / "missing-index.db"

    assert soak_module._state_database_is_valid(missing_state) is False
    assert soak_module._index_database_is_valid(missing_index) is False
    assert not missing_state.exists()
    assert not missing_index.exists()

    stale_state = tmp_path / "stale-state.db"
    with sqlite3.connect(stale_state) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_metadata(key, value) VALUES ('schema_version', '0')"
        )
    assert soak_module._state_database_is_valid(stale_state) is False


def test_soak_runner_exercises_bounded_offline_lifecycle():
    report = run_soak(
        profile="release-candidate",
        duration_seconds=30,
        runs=2,
        parallelism=2,
        seed=17,
        repository_files=8,
    )

    assert report.ok is True
    assert report.profile == "release-candidate"
    assert report.completed_runs == 2
    assert report.successful_runs == 1
    assert report.intentional_cancellations == 1
    assert report.failed_runs == 0
    assert report.tool_calls == 6
    assert report.approval_count == 2
    assert report.mcp_call_count == 2
    assert report.replay_count == 2
    assert report.trace_write_count == 2
    assert report.index_update_count == 2
    assert report.daemon_reconnect_count == 2
    assert report.daemon_restart_count == 2
    assert report.worktree_cleanup_count == 2
    assert report.failed_cleanup_count == 0
    assert report.event_count > 0
    assert report.event_gap_count == 0
    assert report.stale_lease_count == 0
    assert report.leaked_worktree_count == 0
    assert report.leaked_process_count == 0
    assert report.state_database_integrity is True
    assert report.index_database_integrity is True
    assert report.sqlite_bytes_after >= report.sqlite_bytes_before
    assert report.memory_growth_bytes <= report.memory_budget_bytes
    assert report.to_dict()["network_used"] is False
    assert [cycle.cycle for cycle in report.cycles] == [0, 1]
    assert all(cycle.lease_released for cycle in report.cycles)
    assert all(cycle.managed_tool_calls == 3 for cycle in report.cycles)
    assert all(cycle.approval_count == 1 for cycle in report.cycles)
    assert all(cycle.mcp_call_count == 1 for cycle in report.cycles)
    assert all(cycle.trace_written for cycle in report.cycles)

    trends = report.to_dict()["resources"]["trends"]
    assert set(trends) == {
        "process_count",
        "owned_worktree_count",
        "state_database_bytes",
        "index_database_bytes",
        "trace_bytes",
        "memory_bytes",
        "handle_count",
        "thread_count",
    }
    assert trends["process_count"]["before"] == 0
    assert trends["process_count"]["peak"] >= 1
    assert trends["process_count"]["after"] == 0
    assert trends["owned_worktree_count"]["peak"] >= 1
    assert trends["owned_worktree_count"]["after"] == 0
    assert report.to_dict()["resources"]["integrity"] == {
        "state_database": True,
        "repository_index": True,
    }
    for name in (
        "state_database_bytes",
        "index_database_bytes",
        "trace_bytes",
        "memory_bytes",
        "thread_count",
    ):
        assert trends[name]["measurable"] is True
        assert trends[name]["peak"] >= trends[name]["before"]
        assert trends[name]["peak"] >= trends[name]["after"]


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("duration_seconds", 0),
        ("runs", 0),
        ("parallelism", 0),
        ("parallelism", 33),
        ("seed", -1),
        ("repository_files", 0),
    ],
)
def test_soak_runner_rejects_unbounded_or_invalid_options(keyword, value):
    options = {
        "duration_seconds": 1,
        "runs": 1,
        "parallelism": 1,
        "seed": 1,
        "repository_files": 1,
    }
    options[keyword] = value

    with pytest.raises(ValueError):
        run_soak(**options)


def test_soak_reports_managed_cleanup_failures(monkeypatch):
    def fail_cleanup(**_kwargs):
        raise soak_module._SoakCleanupError("synthetic cleanup failure")

    monkeypatch.setattr(soak_module, "_exercise_managed_runtime", fail_cleanup)

    report = run_soak(
        duration_seconds=30,
        runs=1,
        parallelism=1,
        seed=19,
        repository_files=4,
    )

    assert report.ok is False
    assert report.failed_runs == 1
    assert report.failed_cleanup_count == 1
    assert report.cycles[0].cleanup_failure_count == 1
    assert report.cycles[0].error == "synthetic cleanup failure"
    assert report.to_dict()["network_used"] is False


def test_soak_cli_emits_machine_readable_offline_report(capsys):
    exit_code = main(
        [
            "soak",
            "--profile",
            "release-candidate",
            "--duration",
            "30",
            "--runs",
            "1",
            "--parallelism",
            "1",
            "--seed",
            "23",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["profile"] == "release-candidate"
    assert payload["completed_runs"] == 1
    assert payload["operations"]["approvals"] == 1
    assert payload["operations"]["mcp_calls"] == 1
    assert payload["operations"]["daemon_restarts"] == 1
    assert payload["operations"]["worktree_cleanups"] == 1
    assert payload["resources"]["event_gap_count"] == 0
    assert payload["resources"]["trends"]["process_count"]["after"] == 0
    assert payload["network_used"] is False
