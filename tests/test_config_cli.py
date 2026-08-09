import json

from agentbus import cli
from agentbus.configuration import resolve_configuration


def test_config_cli_sets_gets_explains_and_unsets_atomically(tmp_path, capsys):
    config = tmp_path / "config.toml"

    assert cli.main(
        ["config", "set", "max_steps", "8", "--config", str(config), "--json"]
    ) == 0
    changed = json.loads(capsys.readouterr().out)
    assert changed["changed"] is True

    assert cli.main(
        ["config", "get", "max_steps", "--config", str(config), "--json"]
    ) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected["value"] == 8
    assert selected["source"].startswith("explicit:")

    assert cli.main(
        ["config", "explain", "max_steps", "--config", str(config), "--json"]
    ) == 0
    explained = json.loads(capsys.readouterr().out)
    assert explained["precedence"][-1] == "environment"

    assert cli.main(
        ["config", "unset", "max_steps", "--config", str(config), "--json"]
    ) == 0
    assert json.loads(capsys.readouterr().out)["changed"] is True
    assert resolve_configuration(
        config_file=config, discover=False, environ={}
    ).config.max_steps == 12


def test_config_cli_workspace_scope_stays_in_workspace(tmp_path, capsys):
    workspace = tmp_path / "repository"
    workspace.mkdir()

    assert cli.main(
        [
            "config",
            "set",
            "provider_name",
            "deterministic",
            "--scope",
            "workspace",
            "--workspace",
            str(workspace),
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["path"] == str((workspace / ".agentbus" / "config.toml").resolve())
    assert resolve_configuration(
        workspace=workspace,
        user_config_file=tmp_path / "missing.toml",
        environ={},
    ).config.provider_name == "deterministic"


def test_config_cli_path_reports_selected_and_layer_paths(tmp_path, capsys):
    workspace = tmp_path / "repository"
    workspace.mkdir()

    assert cli.main(
        [
            "config",
            "path",
            "--scope",
            "workspace",
            "--workspace",
            str(workspace),
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["selected_scope"] == "workspace"
    assert payload["selected_path"] == str(workspace / ".agentbus" / "config.toml")
    assert payload["dotenv_search"] == "disabled"


def test_config_cli_rejects_invalid_value_without_replacing_file(tmp_path, capsys):
    config = tmp_path / "config.toml"
    assert cli.main(["config", "set", "max_steps", "8", "--config", str(config)]) == 0
    capsys.readouterr()
    before = config.read_bytes()

    assert cli.main(["config", "set", "max_steps", "zero", "--config", str(config)]) == 2
    output = capsys.readouterr().out

    assert "must be an integer" in output
    assert config.read_bytes() == before


def test_config_cli_never_accepts_or_prints_api_keys(tmp_path, capsys):
    secret = "private-value-must-not-appear"

    assert cli.main(
        [
            "config",
            "set",
            "azure_openai_api_key",
            secret,
            "--config",
            str(tmp_path / "config.toml"),
        ]
    ) == 2
    output = capsys.readouterr().out

    assert secret not in output
    assert "secure store" in output
