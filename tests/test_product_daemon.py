import json
import os
from datetime import UTC, datetime
from pathlib import Path

from agentbus.config import AgentBusConfig
from agentbus.control.models import DaemonRegistryEntry
from agentbus.control.registry import (
    DaemonRegistry,
    executable_identity,
    process_start_identity,
)
from agentbus.execution.state_store import StateStore
from agentbus.product.daemon import daemon_status, read_daemon_logs, start_daemon


def _entry(tmp_path, **updates):
    values = {
        "daemon_id": "fixture-daemon",
        "pid": os.getpid(),
        "executable": executable_identity(),
        "process_start_identity": process_start_identity(),
        "host": "127.0.0.1",
        "port": 43210,
        "agentbus_version": "0.6.0b1",
        "started_at": datetime.now(UTC),
        "heartbeat_at": datetime.now(UTC),
        "state_database": str(tmp_path / "state.db"),
        "registry_path": str(tmp_path / "daemons.json"),
        "idle_timeout_seconds": 300,
        "log_path": str(tmp_path / "logs" / "daemon.log"),
    }
    values.update(updates)
    return DaemonRegistryEntry(**values)


def test_daemon_status_exposes_product_lifecycle_without_tokens(tmp_path):
    entry = _entry(tmp_path)
    StateStore(entry.state_database)

    payload = daemon_status(entry)
    rendered = json.dumps(payload)

    assert payload["lifecycle"] == "active"
    assert payload["bound_address"] == "127.0.0.1:43210"
    assert payload["uptime_seconds"] >= 0
    assert payload["active_runs"] == 0
    assert payload["active_index_jobs"] == 0
    assert payload["idle_shutdown"] == {"enabled": True, "timeout_seconds": 300.0}
    assert "token" not in rendered.lower()


def test_start_daemon_returns_matching_owned_daemon_without_spawning(tmp_path, monkeypatch):
    workspace = tmp_path / "repository"
    workspace.mkdir()
    config = AgentBusConfig(
        provider_name="deterministic",
        workspace_dir=str(workspace),
        state_dir=str(tmp_path),
        state_db="state.db",
    )
    registry_path = tmp_path / "daemons.json"
    registry = DaemonRegistry(registry_path)
    registry.register(_entry(tmp_path))

    def fail_spawn(*args, **kwargs):
        raise AssertionError("duplicate daemon start attempted")

    monkeypatch.setattr("agentbus.product.daemon.subprocess.Popen", fail_spawn)

    result = start_daemon(config, registry_path=registry_path)

    assert result.started is False
    assert result.entry.daemon_id == "fixture-daemon"


def test_daemon_logs_are_bounded_and_redacted(tmp_path):
    path = tmp_path / "daemon.log"
    path.write_text(
        "safe line\nAPI_KEY=private-value\nBearer secret-token\nlast line\n",
        encoding="utf-8",
    )

    lines = read_daemon_logs(path, tail=3)

    assert len(lines) == 3
    assert "private-value" not in "\n".join(lines)
    assert "secret-token" not in "\n".join(lines)
    assert lines[-1] == "last line"


def test_registry_entry_records_idle_and_log_metadata(tmp_path):
    entry = _entry(tmp_path)
    payload = entry.model_dump(mode="json")

    assert payload["idle_timeout_seconds"] == 300
    assert payload["log_path"].endswith("daemon.log")


def test_daemon_lifecycle_log_uses_structured_rotating_schema(tmp_path):
    from agentbus.product.daemon import _append_lifecycle_log

    path = tmp_path / "daemon.log"
    _append_lifecycle_log(path, "started", daemon_id="daemon-1", token="private")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["level"] == "info"
    assert payload["component"] == "daemon"
    assert payload["message"] == "started"
    assert payload["fields"]["daemon_id"] == "daemon-1"
    assert payload["fields"]["token"] == "[REDACTED]"
