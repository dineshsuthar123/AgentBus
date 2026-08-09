import json
from pathlib import Path

from agentbus import cli
from agentbus.config import AgentBusConfig
from agentbus.product.upgrade import UpgradeCheckStatus, run_upgrade_check


ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path):
    return AgentBusConfig(
        state_dir=str(tmp_path / "state"),
        state_db="state.db",
    )


def test_upgrade_check_is_offline_and_accepts_absent_optional_state(tmp_path):
    report = run_upgrade_check(_config(tmp_path))

    assert report.ok is True
    assert report.network_used is False
    checks = {check.name: check for check in report.checks}
    assert checks["package"].status == UpgradeCheckStatus.OK
    assert checks["schema:execution-state"].status == UpgradeCheckStatus.OPTIONAL
    assert checks["vscode-extension"].status == UpgradeCheckStatus.OPTIONAL
    assert not (tmp_path / "state").exists()


def test_upgrade_check_accepts_matching_extension_metadata(tmp_path):
    report = run_upgrade_check(
        _config(tmp_path),
        extension_package=ROOT / "extensions" / "vscode" / "package.json",
    )

    extension = next(check for check in report.checks if check.name == "vscode-extension")
    assert extension.status == UpgradeCheckStatus.OK


def test_upgrade_check_rejects_stale_extension_metadata(tmp_path):
    extension = tmp_path / "package.json"
    extension.write_text(
        json.dumps(
            {
                "version": "0.5.0",
                "agentbusCompatibility": {
                    "python": ">=0.5,<0.6",
                    "controlProtocol": "0.9",
                    "stateSchema": 5,
                },
            }
        ),
        encoding="utf-8",
    )

    report = run_upgrade_check(_config(tmp_path), extension_package=extension)

    assert report.ok is False
    extension_check = next(
        check for check in report.checks if check.name == "vscode-extension"
    )
    assert extension_check.status == UpgradeCheckStatus.ERROR
    assert "stale" in extension_check.message or "differ" in extension_check.message


def test_upgrade_check_cli_is_machine_readable_and_never_self_updates(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text(
        "[agentbus]\n" f"state_dir = {json.dumps(str(tmp_path / 'state'))}\n",
        encoding="utf-8",
    )

    assert cli.main(["upgrade-check", "--config", str(config), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["network_used"] is False
    assert payload["self_update_attempted"] is False
