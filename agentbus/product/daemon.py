from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentbus.config import AgentBusConfig
from agentbus.control.models import DaemonRegistryEntry
from agentbus.control.registry import (
    DaemonRegistry,
    process_matches,
    terminate_registered_daemon,
    wait_for_registered_daemon_exit,
)
from agentbus.control.version import CONTROL_PROTOCOL_VERSION
from agentbus.product.logging import ProductLogWriter
from agentbus.security.redaction import redact_text


@dataclass(frozen=True)
class DaemonStartResult:
    entry: DaemonRegistryEntry
    started: bool
    log_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "daemon": daemon_status(self.entry),
            "log_path": str(self.log_path),
            "bearer_token_persisted": False,
        }


def start_daemon(
    config: AgentBusConfig,
    *,
    config_file: str | Path | None = None,
    registry_path: str | Path | None = None,
    idle_timeout: float | None = None,
    log_level: str | None = None,
    startup_timeout: float = 15.0,
) -> DaemonStartResult:
    registry = DaemonRegistry(registry_path)
    active = [entry for entry in registry.list() if process_matches(entry)]
    compatible = [
        entry
        for entry in active
        if entry.protocol_version == CONTROL_PROTOCOL_VERSION
        and Path(entry.state_database).resolve() == config.state_database_path.resolve()
    ]
    log_path = config.state_database_path.resolve().parent / "logs" / "daemon.log"
    if compatible:
        return DaemonStartResult(compatible[-1], started=False, log_path=log_path)
    if active:
        raise RuntimeError(
            "Another active AgentBus daemon is registered for different state or protocol. "
            "Stop it explicitly before starting a replacement."
        )
    timeout = config.daemon_idle_timeout_seconds if idle_timeout is None else idle_timeout
    if timeout < 0:
        raise ValueError("Daemon idle timeout must not be negative")
    selected_level = (log_level or config.log_level).lower()
    if selected_level not in {"error", "warning", "info", "debug", "trace"}:
        raise ValueError("Unsupported daemon log level")
    server_level = "info" if selected_level in {"debug", "trace"} else selected_level
    log_path.parent.mkdir(parents=True, exist_ok=True)
    before = {entry.daemon_id for entry in registry.list()}
    command = [
        sys.executable,
        "-m",
        "agentbus.cli",
        "serve",
        "--json-ready",
        "--registry-path",
        str(registry.path),
        "--idle-timeout",
        str(timeout),
        "--log-level",
        server_level,
    ]
    if config_file is not None:
        command.extend(("--config", str(Path(config_file).expanduser().resolve())))
    creationflags = 0
    start_new_session = os.name != "nt"
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(
        command,
        cwd=config.workspace_path,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        close_fds=True,
        start_new_session=start_new_session,
        creationflags=creationflags,
    )
    deadline = time.monotonic() + startup_timeout
    entry: DaemonRegistryEntry | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        candidates = [
            item
            for item in registry.list()
            if item.daemon_id not in before and item.pid == process.pid and process_matches(item)
        ]
        if candidates:
            entry = candidates[-1]
            break
        time.sleep(0.05)
    if entry is None:
        if process.poll() is None:
            process.terminate()
        _append_lifecycle_log(log_path, "start_failed", pid=process.pid)
        raise RuntimeError(
            "AgentBus daemon did not become ready. Run `agentbus doctor` and verify the ide extra."
        )
    _append_lifecycle_log(log_path, "started", daemon_id=entry.daemon_id, pid=entry.pid)
    return DaemonStartResult(entry=entry, started=True, log_path=log_path)


def stop_daemon(
    *,
    registry_path: str | Path | None = None,
    daemon_id: str | None = None,
    timeout: float = 10.0,
) -> str:
    registry = DaemonRegistry(registry_path)
    entries = registry.list()
    if daemon_id is None:
        active = [entry for entry in entries if process_matches(entry)]
        if len(active) != 1:
            raise RuntimeError(
                "Daemon stop requires DAEMON_ID unless exactly one owned daemon is active."
            )
        daemon_id = active[0].daemon_id
    entry = registry.get(daemon_id)
    if not process_matches(entry):
        raise RuntimeError("Daemon stop refused because process ownership is not proven.")
    terminate_registered_daemon(registry, daemon_id)
    exited = wait_for_registered_daemon_exit(
        registry,
        daemon_id,
        timeout_seconds=timeout,
    )
    if not exited:
        raise RuntimeError("Owned AgentBus daemon did not exit before the timeout.")
    log_path = Path(entry.log_path) if entry.log_path else Path(entry.state_database).parent / "logs" / "daemon.log"
    _append_lifecycle_log(log_path, "stopped", daemon_id=daemon_id, pid=entry.pid)
    return daemon_id


def restart_daemon(
    config: AgentBusConfig,
    *,
    config_file: str | Path | None = None,
    registry_path: str | Path | None = None,
    daemon_id: str | None = None,
    idle_timeout: float | None = None,
    log_level: str | None = None,
) -> DaemonStartResult:
    registry = DaemonRegistry(registry_path)
    active = [entry for entry in registry.list() if process_matches(entry)]
    if active:
        stop_daemon(registry_path=registry.path, daemon_id=daemon_id)
    return start_daemon(
        config,
        config_file=config_file,
        registry_path=registry.path,
        idle_timeout=idle_timeout,
        log_level=log_level,
    )


def daemon_status(entry: DaemonRegistryEntry) -> dict[str, Any]:
    active = process_matches(entry)
    now = datetime.now(UTC)
    started = entry.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    state_path = Path(entry.state_database)
    return {
        **entry.model_dump(mode="json", exclude_none=True),
        "process_matches": active,
        "lifecycle": "active" if active else "stale",
        "uptime_seconds": max(0.0, (now - started).total_seconds()) if active else 0.0,
        "bound_address": f"{entry.host}:{entry.port}",
        "active_runs": _count_active_runs(state_path),
        "active_index_jobs": _count_active_indexes(state_path.parent / "repository-index.sqlite3"),
        "idle_shutdown": {
            "enabled": entry.idle_timeout_seconds > 0,
            "timeout_seconds": entry.idle_timeout_seconds,
        },
        "storage": {
            "state_database": str(state_path),
            "state_bytes": state_path.stat().st_size if state_path.is_file() else 0,
        },
    }


def read_daemon_logs(path: str | Path, *, tail: int = 100) -> list[str]:
    if tail < 1 or tail > 10_000:
        raise ValueError("Daemon log tail must be between 1 and 10000")
    log_path = Path(path).expanduser().resolve()
    if not log_path.is_file():
        return []
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [redact_text(line, max_chars=4_000) or "" for line in lines[-tail:]]


def _count_active_runs(path: Path) -> int:
    return _count_query(
        path,
        "SELECT COUNT(*) FROM runs WHERE status IN ('pending', 'running', 'awaiting_approval', 'integrating')",
    )


def _count_active_indexes(path: Path) -> int:
    return _count_query(path, "SELECT COUNT(*) FROM index_operations")


def _count_query(path: Path, query: str) -> int:
    if not path.is_file():
        return 0
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute(query).fetchone()
            return int(row[0]) if row else 0
    except (sqlite3.Error, OSError, ValueError):
        return 0


def _append_lifecycle_log(path: Path, event: str, **fields: Any) -> None:
    ProductLogWriter(path).write(
        level="error" if event.endswith("failed") else "info",
        component="daemon",
        message=event,
        fields=fields,
    )
