from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentbus.config import AgentBusConfig
from agentbus.control.models import DaemonRegistryEntry
from agentbus.control.registry import (
    DaemonRegistry,
    executable_identity,
    process_start_identity,
)
from agentbus.execution.models import RunRecord, RunStatus, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.product.cleanup import CleanupMode, RuntimeCleanup
from agentbus.replay.session import ReplaySessionStatus
from agentbus.worktrees.manager import GitWorktreeManager


def _config(root: Path, workspace: Path) -> AgentBusConfig:
    return AgentBusConfig(
        workspace_dir=str(workspace),
        state_dir=str(root),
        state_db="state.db",
        runs_dir=str(root / "runs"),
        provider_name="deterministic",
    )


def _run(run_id: str, workspace: Path, status: RunStatus, now: datetime) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        original_task="Cleanup test",
        model="deterministic",
        workspace=str(workspace),
        status=status,
        created_at=now,
        updated_at=now,
        completed_at=now if status in {RunStatus.SUCCEEDED, RunStatus.FAILED} else None,
        planner_output={"goal": "test", "steps": []},
        graph_data={"version": 1, "tasks": []},
    )


def _daemon(path: Path, *, active: bool) -> DaemonRegistryEntry:
    now = datetime.now(UTC)
    return DaemonRegistryEntry(
        daemon_id="active" if active else "dead",
        pid=os.getpid() if active else 2_000_000_000,
        executable=executable_identity() if active else str(path / "missing-python"),
        process_start_identity=(process_start_identity() if active else "missing"),
        host="127.0.0.1",
        port=43123,
        agentbus_version="0.6.0b1",
        started_at=now,
        heartbeat_at=now,
        state_database=str(path.parent / "state.db"),
        registry_path=str(path),
    )


def _runtime(tmp_path: Path):
    workspace = tmp_path / "user-repository"
    workspace.mkdir()
    (workspace / ".env").write_text("API_KEY=preserve\n", encoding="utf-8")
    root = tmp_path / "runtime"
    runs = root / "runs"
    runs.mkdir(parents=True)
    config = _config(root, workspace)
    store = StateStore(config.state_database_path)
    old = datetime.now(UTC) - timedelta(days=60)
    store.create_run(_run("terminal-run", workspace, RunStatus.SUCCEEDED, old))
    store.create_run(_run("active-run", workspace, RunStatus.RUNNING, old))
    terminal_log = runs / "20260101_000000_terminal-run.jsonl"
    active_log = runs / "20260101_000000_active-run.jsonl"
    unknown_log = runs / "20260101_000000_unknown-run.jsonl"
    for path in (terminal_log, active_log, unknown_log):
        path.write_text("{}\n", encoding="utf-8")
    registry_path = root / "daemons.json"
    registry = DaemonRegistry(registry_path)
    registry.register(_daemon(registry_path, active=True))
    registry.register(_daemon(registry_path, active=False))
    return config, registry_path, workspace, terminal_log, active_log, unknown_log


def test_cleanup_dry_run_matches_safe_terminal_and_daemon_scope(tmp_path):
    config, registry_path, workspace, terminal, active, unknown = _runtime(tmp_path)
    cleanup = RuntimeCleanup(config, registry_path=registry_path)

    result = cleanup.run(mode=CleanupMode.STALE, dry_run=True)

    indexed = {(item.category, item.identifier): item for item in result.items}
    assert indexed[("daemon_registry", "dead")].status == "planned"
    assert indexed[("daemon_registry", "active")].status == "protected"
    assert indexed[("run_log", "terminal-run")].status == "planned"
    assert indexed[("run_log", "active-run")].status == "protected"
    assert indexed[("run_log", "unknown-run")].status == "protected"
    assert terminal.is_file() and active.is_file() and unknown.is_file()
    assert (workspace / ".env").read_text(encoding="utf-8") == "API_KEY=preserve\n"
    assert {entry.daemon_id for entry in DaemonRegistry(registry_path).list()} == {
        "active",
        "dead",
    }


def test_cleanup_removes_dead_metadata_and_terminal_logs_only(tmp_path):
    config, registry_path, workspace, terminal, active, unknown = _runtime(tmp_path)
    cleanup = RuntimeCleanup(config, registry_path=registry_path)

    result = cleanup.run(mode=CleanupMode.STALE)

    assert result.ok is True
    assert terminal.exists() is False
    assert active.is_file()
    assert unknown.is_file()
    assert (workspace / ".env").read_text(encoding="utf-8") == "API_KEY=preserve\n"
    assert [entry.daemon_id for entry in DaemonRegistry(registry_path).list()] == [
        "active"
    ]
    payload = result.to_dict()
    assert payload["network_used"] is False
    assert "user repositories" in payload["protected_data"]


def test_normal_cleanup_does_not_remove_run_logs(tmp_path):
    config, registry_path, _workspace, terminal, active, unknown = _runtime(tmp_path)

    result = RuntimeCleanup(config, registry_path=registry_path).run()

    assert terminal.is_file() and active.is_file() and unknown.is_file()
    assert not any(item.category == "run_log" for item in result.items)


def test_cleanup_removes_clean_terminal_worktree_but_preserves_dirty_one(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "AgentBus Test")
    _git(repository, "config", "user.email", "agentbus@example.invalid")
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-q", "-m", "baseline")
    base = _git(repository, "rev-parse", "HEAD")
    root = tmp_path / "runtime"
    config = AgentBusConfig(
        workspace_dir=str(repository),
        state_dir=str(root),
        state_db="state.db",
        runs_dir=str(root / "runs"),
        worktree_root=str(tmp_path / "worktrees"),
        provider_name="deterministic",
    )
    store = StateStore(config.state_database_path)
    old = datetime.now(UTC) - timedelta(days=60)
    store.create_run_with_tasks(
        _run("terminal-run", repository, RunStatus.SUCCEEDED, old),
        [
            TaskSpec(task_id="clean", title="Clean", description="Clean worktree"),
            TaskSpec(task_id="dirty", title="Dirty", description="Dirty worktree"),
        ],
    )
    manager = GitWorktreeManager(repository, config.worktree_root_path, store)
    clean = manager.create_task_worktree(
        "terminal-run", "clean", base, "worker-clean"
    )
    dirty = manager.create_task_worktree(
        "terminal-run", "dirty", base, "worker-dirty"
    )
    (Path(dirty.path) / "untracked.txt").write_text("preserve\n", encoding="utf-8")
    assert _git(Path(clean.path), "status", "--porcelain=v1") == ""

    result = RuntimeCleanup(
        config,
        registry_path=root / "daemons.json",
    ).run(mode=CleanupMode.STALE)

    items = {
        item.identifier: item
        for item in result.items
        if item.category == "worktree"
    }
    assert items[clean.worktree_id].status == "removed", items[clean.worktree_id]
    assert items[dirty.worktree_id].status == "protected"
    assert Path(clean.path).exists() is False
    assert (Path(dirty.path) / "untracked.txt").read_text(encoding="utf-8") == (
        "preserve\n"
    )
    assert (repository / "tracked.txt").read_text(encoding="utf-8") == "baseline\n"


def test_terminal_replay_cleanup_refuses_unknown_files(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    root = tmp_path / ".agentbus-replays" / workspace.name
    known = root / "replay-known"
    unknown = root / "replay-unknown"
    known.mkdir(parents=True)
    unknown.mkdir()
    (known / "state.db").write_bytes(b"known-state")
    (unknown / "state.db").write_bytes(b"known-state")
    (unknown / "notes.txt").write_text("user data\n", encoding="utf-8")
    terminal = type(
        "Replay",
        (),
        {
            "replay_id": "replay-known",
            "source_run_id": "run-1",
            "status": ReplaySessionStatus.SUCCEEDED,
        },
    )()
    protected = type(
        "Replay",
        (),
        {
            "replay_id": "replay-unknown",
            "source_run_id": "run-1",
            "status": terminal.status,
        },
    )()

    class Store:
        def list_replay_sessions(self, *, limit):
            assert limit == 1_000
            return [terminal, protected]

        def get_run(self, run_id):
            assert run_id == "run-1"
            return type("Run", (), {"workspace": str(workspace)})()

        def get_replay_session(self, replay_id):
            return terminal if replay_id == terminal.replay_id else protected

    config = _config(tmp_path / "runtime", workspace)
    cleanup = RuntimeCleanup(config, registry_path=tmp_path / "registry.json")

    items = cleanup._clean_replay_workspaces(Store(), dry_run=False)

    indexed = {item.identifier: item for item in items}
    assert indexed["replay-known"].status == "removed"
    assert indexed["replay-unknown"].status == "protected"
    assert known.exists() is False
    assert (unknown / "notes.txt").read_text(encoding="utf-8") == "user data\n"


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    ).stdout.strip()
