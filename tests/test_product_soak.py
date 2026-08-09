from __future__ import annotations

import json

import pytest

from agentbus.cli import main
from agentbus.product.soak import run_soak


def test_soak_runner_exercises_bounded_offline_lifecycle():
    report = run_soak(
        duration_seconds=30,
        runs=2,
        parallelism=2,
        seed=17,
        repository_files=8,
    )

    assert report.ok is True
    assert report.completed_runs == 2
    assert report.successful_runs == 1
    assert report.intentional_cancellations == 1
    assert report.failed_runs == 0
    assert report.tool_calls == 2
    assert report.replay_count == 2
    assert report.index_update_count == 2
    assert report.daemon_reconnect_count == 2
    assert report.worktree_cleanup_count == 2
    assert report.failed_cleanup_count == 0
    assert report.event_count > 0
    assert report.event_gap_count == 0
    assert report.stale_lease_count == 0
    assert report.leaked_worktree_count == 0
    assert report.leaked_process_count == 0
    assert report.sqlite_bytes_after >= report.sqlite_bytes_before
    assert report.memory_growth_bytes <= report.memory_budget_bytes
    assert report.to_dict()["network_used"] is False
    assert [cycle.cycle for cycle in report.cycles] == [0, 1]
    assert all(cycle.lease_released for cycle in report.cycles)


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


def test_soak_cli_emits_machine_readable_offline_report(capsys):
    exit_code = main(
        [
            "soak",
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
    assert payload["completed_runs"] == 1
    assert payload["operations"]["worktree_cleanups"] == 1
    assert payload["resources"]["event_gap_count"] == 0
    assert payload["network_used"] is False
