import json
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentbus import __version__
from agentbus import cli
from agentbus.bootstrap import BootstrapError, initialize
from agentbus.config import AgentBusConfig
from agentbus.configuration import resolve_configuration
from agentbus.doctor import CheckStatus, run_doctor
from agentbus.execution.schema import SCHEMA_VERSION
from agentbus.execution.state_store import StateStore
from agentbus.mcp import mcp_server_capabilities
from agentbus.tools.protocol import ToolResourceBudget


def _git_repository(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, shell=False)
    subprocess.run(
        ["git", "config", "user.name", "AgentBus Tests"],
        cwd=path,
        check=True,
        shell=False,
    )
    subprocess.run(
        ["git", "config", "user.email", "tests@agentbus.invalid"],
        cwd=path,
        check=True,
        shell=False,
    )
    (path / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, shell=False)
    subprocess.run(
        ["git", "commit", "-q", "-m", "baseline"],
        cwd=path,
        check=True,
        shell=False,
    )
    return path


def test_release_cli_version_help_and_legacy_forwarding(monkeypatch, capsys):
    assert cli.main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out

    assert cli.main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "release-report" in output
    assert "doctor" in output

    forwarded = []
    monkeypatch.setattr(cli, "_legacy", lambda arguments: forwarded.append(arguments) or 0)
    assert cli.main(["old style task", "--workflow", "multi"]) == 0
    assert forwarded == [["old style task", "--workflow", "multi"]]


def test_config_precedence_and_secret_safe_sources(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[agentbus]\nmax_steps = 3\nmodel_name = \"from-file\"\n",
        encoding="utf-8",
    )
    resolved = resolve_configuration(
        config_file=config_file,
        environ={
            "AGENTBUS_MAX_STEPS": "5",
            "AZURE_OPENAI_API_KEY": "top-secret-value",
        },
        cli_overrides={"max_steps": 7},
    )

    assert resolved.config.max_steps == 5
    assert resolved.config.model_name == "from-file"
    assert resolved.sources["max_steps"] == "environment:AGENTBUS_MAX_STEPS"
    assert "top-secret-value" not in json.dumps(resolved.safe_values())
    assert resolved.safe_values()["azure_openai_api_key"]["value"] == "[configured]"


def test_config_file_unknown_values_are_rejected(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[agentbus]\nunknown_option = true\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown AgentBus config"):
        resolve_configuration(config_file=config_file, environ={})


def test_config_file_loads_validated_tool_resource_budget(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "agentbus": {
                    "tool_resource_budget": {
                        "wall_clock_seconds": 20,
                        "stdout_bytes": 1_024,
                        "stderr_bytes": 1_024,
                        "combined_output_bytes": 2_048,
                        "invocations_per_task": 2,
                        "invocations_per_run": 4,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_configuration(config_file=config_file, environ={})

    assert isinstance(resolved.config.tool_resource_budget, ToolResourceBudget)
    assert resolved.config.tool_resource_budget.wall_clock_seconds == 20
    assert resolved.config.tool_resource_budget.invocations_per_task == 2
    assert resolved.safe_values()["tool_resource_budget"]["value"] == (
        resolved.config.tool_resource_budget.model_dump(mode="json")
    )


def test_invalid_tool_budget_error_does_not_echo_input(tmp_path: Path) -> None:
    private_value = "budget-secret-must-not-appear"
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "tool_resource_budget": {
                    "wall_clock_seconds": private_value,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid tool resource budget") as exc:
        resolve_configuration(config_file=config_file, environ={})

    assert private_value not in str(exc.value)


def test_config_file_loads_mcp_servers_without_exposing_private_values(
    tmp_path: Path,
) -> None:
    private_argument = "private-mcp-command-argument"
    private_environment = "private-mcp-environment-value"
    capabilities = [
        item.model_dump(mode="json")
        for item in mcp_server_capabilities("fixture")
    ]
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "agentbus": {
                    "mcp_server_configs": [
                        {
                            "server_id": "fixture",
                            "transport": "stdio",
                            "executable_alias": "python",
                            "arguments": ["--marker", private_argument],
                            "environment": {"CI": private_environment},
                            "capability_map": {"echo": capabilities},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_configuration(config_file=config_file, environ={})

    server = resolved.config.mcp_server_configs[0]
    assert server.server_id == "fixture"
    assert server.arguments == ("--marker", private_argument)
    safe = json.dumps(resolved.safe_values(), sort_keys=True)
    assert private_argument not in safe
    assert private_environment not in safe
    assert resolved.safe_values()["mcp_server_configs"]["value"] == [
        {
            "server_id": "fixture",
            "transport": "stdio",
            "configured_tools": ["echo"],
        }
    ]


def test_invalid_mcp_config_error_does_not_echo_secret_input(tmp_path: Path) -> None:
    private_value = "mcp-secret-must-not-appear"
    capabilities = [
        item.model_dump(mode="json")
        for item in mcp_server_capabilities("fixture")
    ]
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "mcp_server_configs": [
                    {
                        "server_id": "fixture",
                        "transport": "stdio",
                        "executable_alias": "python",
                        "environment": {"AZURE_OPENAI_API_KEY": private_value},
                        "capability_map": {"echo": capabilities},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid MCP server configuration") as exc:
        resolve_configuration(config_file=config_file, environ={})

    assert private_value not in str(exc.value)


def test_init_dry_run_creates_nothing_and_actual_init_is_safe(tmp_path):
    root = tmp_path / "state"
    dry = initialize(
        workspace=tmp_path,
        local=True,
        provider="azure",
        dry_run=True,
        root=root,
        with_env_example=True,
    )
    assert dry.dry_run is True
    assert not root.exists()

    actual = initialize(
        workspace=tmp_path,
        local=True,
        provider="azure",
        root=root,
        with_env_example=True,
    )
    assert StateStore(actual.state_database).schema_version == SCHEMA_VERSION
    config_text = actual.config_file.read_text(encoding="utf-8")
    environment_text = (root / ".env.example").read_text(encoding="utf-8")
    assert "API_KEY" not in config_text
    assert "replace-with-a-real-key" in environment_text
    assert "network" not in config_text.lower()

    with pytest.raises(BootstrapError, match="already exists"):
        initialize(workspace=tmp_path, root=root)


def test_init_cli_json_reports_no_credentials_or_network(tmp_path, capsys):
    root = tmp_path / "bootstrap"
    assert cli.main(["init", "--root", str(root), "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["network_used"] is False
    assert payload["credentials_created"] is False
    assert not root.exists()


def test_doctor_is_offline_and_accepts_isolated_repository(tmp_path, monkeypatch):
    repository = _git_repository(tmp_path / "repository")

    class NetworkSentinel:
        def __init__(self, *args, **kwargs):
            pytest.fail("offline doctor constructed a provider router")

    monkeypatch.setattr("agentbus.doctor.ModelRouter", NetworkSentinel)
    report = run_doctor(
        AgentBusConfig(
            workspace_dir=str(repository),
            state_dir=str(tmp_path / "state"),
            runs_dir=str(tmp_path / "runs"),
        )
    )

    assert report.network_used is False
    assert report.status != CheckStatus.FAIL
    assert next(item for item in report.checks if item.name == "git-boundary").status == CheckStatus.PASS


def test_doctor_reports_invalid_workspace_and_missing_git(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentbus.doctor.shutil.which",
        lambda name: None if name == "git" else "optional",
    )
    report = run_doctor(
        AgentBusConfig(
            workspace_dir=str(tmp_path / "missing"),
            state_dir=str(tmp_path / "state"),
            runs_dir=str(tmp_path / "runs"),
        )
    )
    checks = {item.name: item for item in report.checks}

    assert checks["git"].status == CheckStatus.FAIL
    assert checks["workspace"].status == CheckStatus.FAIL


def test_doctor_selected_azure_missing_values_is_failure(tmp_path):
    repository = _git_repository(tmp_path / "repository")
    report = run_doctor(
        AgentBusConfig(
            provider_name="azure",
            workspace_dir=str(repository),
            state_dir=str(tmp_path / "state"),
            runs_dir=str(tmp_path / "runs"),
        )
    )
    check = next(item for item in report.checks if item.name == "provider:azure")

    assert check.status == CheckStatus.FAIL
    assert "API_KEY" not in json.dumps(report.to_dict())


def test_doctor_warns_for_stale_leases_and_orphaned_worktrees(tmp_path):
    repository = _git_repository(tmp_path / "repository")
    state_path = tmp_path / "state.db"
    StateStore(state_path)
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(state_path) as connection:
        connection.execute(
            """INSERT INTO worker_leases(
                lease_id, run_id, task_id, worker_id, status, acquired_at,
                heartbeat_at, expires_at, released_at, fencing_token, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("lease", "run", "task", "worker", "active", now, now, expired, None, 1, "{}"),
        )
        connection.execute(
            """INSERT INTO worktrees(
                worktree_id, run_id, task_id, path, repository_root, base_commit,
                branch_ref, purpose, status, worker_id, result_commit, created_at,
                updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "worktree",
                "run",
                "task",
                str(tmp_path / "missing-worktree"),
                str(repository),
                "a" * 40,
                "refs/agentbus/test",
                "task",
                "active",
                None,
                None,
                now,
                now,
                "{}",
            ),
        )
        connection.commit()

    report = run_doctor(
        AgentBusConfig(
            workspace_dir=str(repository),
            state_db=str(state_path),
            state_dir=str(tmp_path),
            runs_dir=str(tmp_path / "runs"),
        )
    )
    checks = {item.name: item for item in report.checks}
    assert checks["stale-leases"].status == CheckStatus.WARN
    assert checks["orphaned-worktrees"].status == CheckStatus.WARN


def test_config_show_json_never_prints_secret(monkeypatch, capsys):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "must-not-appear")

    assert cli.main(["config", "show", "--json"]) == 0
    output = capsys.readouterr().out
    assert "must-not-appear" not in output
    assert "[configured]" in output


def test_config_paths_and_doctor_json_are_machine_readable(tmp_path, capsys):
    repository = _git_repository(tmp_path / "repository")
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[agentbus]\n"
        f"workspace_dir = {json.dumps(str(repository))}\n"
        f"state_dir = {json.dumps(str(tmp_path / 'state'))}\n"
        f"runs_dir = {json.dumps(str(tmp_path / 'runs'))}\n",
        encoding="utf-8",
    )

    assert cli.main(["config", "paths", "--config", str(config_file), "--json"]) == 0
    paths = json.loads(capsys.readouterr().out)
    assert paths["workspace"] == str(repository.resolve())
    assert paths["dotenv_search"] == "disabled"

    assert cli.main(["doctor", "--config", str(config_file), "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["network_used"] is False
    assert doctor["status"] in {"OK", "WARNING"}
