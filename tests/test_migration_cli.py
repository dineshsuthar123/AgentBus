import json
import sqlite3

from agentbus import cli
from agentbus.execution.schema import SCHEMA_VERSION
from agentbus.execution.state_store import StateStore


def _config(path):
    config = path / "config.toml"
    config.write_text(
        "[agentbus]\n"
        f"state_dir = {json.dumps(str(path / 'state'))}\n"
        "state_db = 'state.db'\n",
        encoding="utf-8",
    )
    return config


def test_migration_cli_status_and_plan_are_non_destructive(tmp_path, capsys):
    config = _config(tmp_path)

    assert cli.main(["migrate", "status", "--config", str(config), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert cli.main(["migrate", "plan", "--config", str(config), "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)

    assert status["network_used"] is False
    assert {item["state"] for item in status["targets"]} == {"absent"}
    assert plan["dry_run"] is True
    assert not (tmp_path / "state" / "state.db").exists()


def test_migration_cli_verifies_current_database(tmp_path, capsys):
    config = _config(tmp_path)
    database = tmp_path / "state" / "state.db"
    StateStore(database)

    assert cli.main(["migrate", "verify", "--config", str(config), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    target = next(item for item in payload["targets"] if item["name"] == "execution-state")
    assert target["state"] == "current"
    assert target["current_version"] == SCHEMA_VERSION


def test_migration_cli_rejects_newer_database(tmp_path, capsys):
    config = _config(tmp_path)
    database = tmp_path / "state" / "state.db"
    StateStore(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_metadata SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION + 1),),
        )
        connection.commit()

    assert cli.main(["migrate", "apply", "--config", str(config), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert "newer" in payload["error"]
    assert payload["network_used"] is False
