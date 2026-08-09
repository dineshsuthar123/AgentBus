from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentbus.config import AgentBusConfig
from agentbus.control.models import DaemonRegistryEntry
from agentbus.control.registry import (
    DaemonRegistry,
    executable_identity,
    process_start_identity,
)
from agentbus.execution.models import RunRecord, RunStatus
from agentbus.execution.state_store import StateStore
from agentbus.product.cleanup import CleanupMode, RuntimeCleanup


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
