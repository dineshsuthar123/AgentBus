from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from agentbus import main as main_module
from agentbus.config import AgentBusConfig
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.leases import LeaseService, LeaseStatus
from agentbus.execution.models import TaskStatus
from agentbus.execution.state_store import StateStore
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.models import WorktreeStatus


PLAN = {
    "goal": "Parallel CLI",
    "steps": [
        {
            "id": "task-A",
            "title": "Task A",
            "description": "Implement A",
            "risk": "low",
        }
    ],
    "test_strategy": "offline",
    "done_criteria": ["complete"],
}


def git(path, *arguments):
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout.strip()


def setup_repository(path):
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "AgentBus Test")
    git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "README.md").write_text("baseline\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-q", "-m", "baseline")
    return path.resolve(), git(path, "rev-parse", "HEAD")


def settings(tmp_path, workspace):
    return AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        worktree_root=str(tmp_path / "worktrees"),
    )


def create_run(config, base):
    store = StateStore(config.state_database_path)
    DurableExecutionEngine(store).create_run(
        "Parallel CLI",
        PLAN,
        model="fake",
        workspace=config.workspace_dir,
        run_id="parallel-run",
        metadata={
            "parallel_execution": {
                "enabled": True,
                "max_workers": 2,
                "base_commit": base,
                "worktree_root": str(config.worktree_root_path),
                "keep_worktrees": True,
                "lease_seconds": 60,
            }
        },
    )
    return store


def patch_config(monkeypatch, config):
    monkeypatch.setattr(main_module.AgentBusConfig, "from_env", lambda: config)


def test_parallel_flags_parse_and_require_durable_multi(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agentbus.main",
            "--workflow",
            "multi",
            "--durable",
            "--parallel",
            "--max-workers",
            "3",
            "Task",
        ],
    )
    args = main_module.parse_args()
    assert args.parallel is True
    assert args.max_workers == 3

    monkeypatch.setattr(sys, "argv", ["agentbus.main", "--parallel", "Task"])
    with pytest.raises(SystemExit) as captured:
        main_module.parse_args()
    assert captured.value.code == 2


@pytest.mark.parametrize("value", ["0", "-1"])
def test_max_workers_must_be_positive(monkeypatch, value):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agentbus.main",
            "--workflow",
            "multi",
            "--durable",
            "--parallel",
            "--max-workers",
            value,
            "Task",
        ],
    )
    with pytest.raises(SystemExit) as captured:
        main_module.parse_args()
    assert captured.value.code == 2


def test_cli_lists_worktrees_workers_and_scheduler_without_prompt(
    monkeypatch, capsys, tmp_path
):
    source, base = setup_repository(tmp_path / "repo")
    config = settings(tmp_path, source)
    store = create_run(config, base)
    manager = GitWorktreeManager(source, config.worktree_root_path, store)
    worktree = manager.create_integration_worktree("parallel-run", base)
    store.update_task_status(
        "parallel-run", "task-A", TaskStatus.READY, event_type="test_ready"
    )
    lease = LeaseService(store).acquire_lease(
        "parallel-run", "task-A", "worker-one", activate_task=True
    )
    patch_config(monkeypatch, config)
    monkeypatch.setattr(
        "builtins.input", lambda prompt: pytest.fail("diagnostics must not prompt")
    )

    monkeypatch.setattr(
        sys, "argv", ["agentbus.main", "--list-worktrees", "parallel-run"]
    )
    assert main_module.main() == 0
    assert worktree.path in capsys.readouterr().out

    monkeypatch.setattr(
        sys, "argv", ["agentbus.main", "--list-workers", "parallel-run"]
    )
    assert main_module.main() == 0
    output = capsys.readouterr().out
    assert "worker-one" in output
    assert f"token={lease.fencing_token}" in output

    monkeypatch.setattr(
        sys, "argv", ["agentbus.main", "--show-scheduler", "parallel-run"]
    )
    assert main_module.main() == 0
    output = capsys.readouterr().out
    assert "Parallel execution: enabled" in output
    assert "Configured max workers: 2" in output
    assert "Active lease: worker-one -> task-A" in output


def test_cli_recover_leases_expires_only_stale_leases(monkeypatch, capsys, tmp_path):
    source, base = setup_repository(tmp_path / "repo")
    config = settings(tmp_path, source)
    store = create_run(config, base)
    store.update_task_status(
        "parallel-run", "task-A", TaskStatus.READY, event_type="test_ready"
    )
    old_clock = lambda: datetime(2000, 1, 1, tzinfo=timezone.utc)
    lease = LeaseService(store, lease_seconds=1, clock=old_clock).acquire_lease(
        "parallel-run", "task-A", "expired-worker", activate_task=True
    )
    patch_config(monkeypatch, config)
    monkeypatch.setattr(
        sys, "argv", ["agentbus.main", "--recover-leases", "parallel-run"]
    )

    assert main_module.main() == 0
    assert "Expired stale leases: 1" in capsys.readouterr().out
    assert LeaseService(store).get_lease(lease.lease_id).status == LeaseStatus.EXPIRED


def test_cli_cleanup_removes_clean_and_refuses_dirty_worktree(
    monkeypatch, capsys, tmp_path
):
    source, base = setup_repository(tmp_path / "repo")
    config = settings(tmp_path, source)
    store = create_run(config, base)
    manager = GitWorktreeManager(source, config.worktree_root_path, store)
    clean = manager.create_integration_worktree("parallel-run", base)
    dirty = manager.create_task_worktree(
        "parallel-run", "task-A", base, "worker-one"
    )
    dirty_path = __import__("pathlib").Path(dirty.path)
    (dirty_path / "dirty.txt").write_text("preserve me\n", encoding="utf-8")
    patch_config(monkeypatch, config)
    monkeypatch.setattr(
        sys, "argv", ["agentbus.main", "--cleanup-worktrees", "parallel-run"]
    )

    assert main_module.main() == 1
    output = capsys.readouterr().out
    assert f"Removed: {clean.path}" in output
    assert f"Refused: {dirty.path}" in output
    assert store.get_worktree(clean.worktree_id).status == WorktreeStatus.REMOVED
    assert store.get_worktree(dirty.worktree_id).status == WorktreeStatus.CLEANUP_PENDING
    assert (dirty_path / "dirty.txt").read_text(encoding="utf-8") == "preserve me\n"
    assert git(source, "status", "--porcelain") == ""
