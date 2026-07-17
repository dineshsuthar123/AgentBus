from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from agentbus import cli
from agentbus.config import AgentBusConfig


def test_root_help_lists_control_plane_commands(capsys) -> None:
    assert cli.main(["--help"]) == 0

    output = capsys.readouterr().out
    assert "serve" in output
    assert "daemon" in output
    assert "control-schema" in output


def test_serve_forwards_secure_defaults_without_importing_eagerly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured = {}
    module = ModuleType("agentbus.control.server")

    def fake_serve(**kwargs):
        captured.update(kwargs)
        return 0

    module.serve = fake_serve
    monkeypatch.setitem(sys.modules, "agentbus.control.server", module)
    monkeypatch.setattr(
        cli,
        "resolve_configuration",
        lambda **_kwargs: SimpleNamespace(
            config=AgentBusConfig(
                workspace_dir=str(tmp_path),
                state_db=str(tmp_path / "state.db"),
            )
        ),
    )

    result = cli.main(
        [
            "serve",
            "--port",
            "0",
            "--json-ready",
            "--registry-path",
            str(tmp_path / "registry.json"),
        ]
    )

    assert result == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 0
    assert captured["json_ready"] is True


def test_serve_dependency_error_is_actionable_and_keeps_stdout_clean(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = ModuleType("agentbus.control.server")

    def missing_dependencies(**_kwargs):
        raise RuntimeError('Install "agentbus[ide]".')

    module.serve = missing_dependencies
    monkeypatch.setitem(sys.modules, "agentbus.control.server", module)
    monkeypatch.setattr(
        cli,
        "resolve_configuration",
        lambda **_kwargs: SimpleNamespace(config=AgentBusConfig()),
    )

    result = cli.main(["serve", "--json-ready"])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert 'agentbus[ide]' in captured.err


def test_daemon_registry_json_contains_no_token_fields(
    tmp_path: Path,
    capsys,
) -> None:
    registry = tmp_path / "daemons.json"

    result = cli.main(
        [
            "daemon",
            "--registry-path",
            str(registry),
            "--json",
            "registry",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["daemons"] == []
    assert "token" not in json.dumps(payload).lower()


def test_daemon_cleanup_stale_is_idempotent(tmp_path: Path, capsys) -> None:
    registry = tmp_path / "daemons.json"

    first = cli.main(
        ["daemon", "--registry-path", str(registry), "cleanup-stale"]
    )
    second = cli.main(
        ["daemon", "--registry-path", str(registry), "cleanup-stale"]
    )

    assert first == second == 0
    assert "Removed stale daemon registrations: 0" in capsys.readouterr().out
