from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentbus.control.errors import ControlPlaneConflictError
from agentbus.control.models import DaemonRegistryEntry
from agentbus.control.registry import (
    DaemonRegistry,
    executable_identity,
    process_matches,
    process_start_identity,
    terminate_registered_daemon,
)


def _entry(path: Path, *, daemon_id: str = "daemon-1") -> DaemonRegistryEntry:
    now = datetime.now(timezone.utc)
    return DaemonRegistryEntry(
        daemon_id=daemon_id,
        pid=os.getpid(),
        executable=executable_identity(),
        process_start_identity=process_start_identity(),
        host="127.0.0.1",
        port=43123,
        agentbus_version="0.2.0",
        started_at=now,
        heartbeat_at=now,
        state_database=str(path.parent / "state.db"),
        registry_path=str(path),
    )


def test_registry_persists_only_non_secret_metadata(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    registry = DaemonRegistry(path)
    registry.register(_entry(path))

    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload).lower()

    assert payload["version"] == 1
    assert payload["daemons"][0]["daemon_id"] == "daemon-1"
    assert "token" not in serialized
    assert "authorization" not in serialized


def test_registry_heartbeat_and_remove_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    registry = DaemonRegistry(path)
    registry.register(_entry(path))
    heartbeat = datetime(2030, 1, 1, tzinfo=timezone.utc)

    registry.heartbeat("daemon-1", heartbeat)

    assert registry.get("daemon-1").heartbeat_at == heartbeat
    assert registry.remove("daemon-1") is True
    assert registry.remove("daemon-1") is False


def test_current_process_identity_matches_registry_entry(tmp_path: Path) -> None:
    entry = _entry(tmp_path / "registry.json")

    assert entry.process_start_identity
    assert entry.executable
    assert process_matches(entry) is True


def test_external_process_identity_is_stable() -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
        shell=False,
    )
    try:
        now = datetime.now(timezone.utc)
        entry = DaemonRegistryEntry(
            daemon_id="external-process",
            pid=process.pid,
            executable=executable_identity(process.pid),
            process_start_identity=process_start_identity(process.pid),
            host="127.0.0.1",
            port=43123,
            agentbus_version="0.2.0",
            started_at=now,
            heartbeat_at=now,
            state_database="state.db",
            registry_path="registry.json",
        )

        assert entry.executable
        assert entry.process_start_identity
        assert process_matches(entry) is True
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_cleanup_removes_stale_identity_without_stopping_process(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.json"
    registry = DaemonRegistry(path)
    stale = _entry(path).model_copy(update={"process_start_identity": "stale"})
    registry.register(stale)

    assert registry.cleanup_stale() == ["daemon-1"]
    assert registry.list() == []


def test_safe_termination_refuses_pid_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "registry.json"
    registry = DaemonRegistry(path)
    registry.register(_entry(path))
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr("agentbus.control.registry.process_matches", lambda entry: False)
    monkeypatch.setattr(
        "agentbus.control.registry.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )

    with pytest.raises(ControlPlaneConflictError, match="no process was stopped"):
        terminate_registered_daemon(registry, "daemon-1")

    assert killed == []
    assert registry.list() == []
