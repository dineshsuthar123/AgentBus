import json
import subprocess

from agentbus import cli
from agentbus.config import AgentBusConfig
from agentbus.doctor import CheckStatus, render_doctor, run_doctor


def _repository(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, shell=False)
    return path


def _config(tmp_path):
    return AgentBusConfig(
        provider_name="deterministic",
        workspace_dir=str(_repository(tmp_path / "repository")),
        state_dir=str(tmp_path / "state"),
        state_db="state.db",
        runs_dir=str(tmp_path / "runs"),
    )


def test_doctor_exposes_complete_product_status_vocabulary():
    assert {status.value for status in CheckStatus} == {
        "OK",
        "WARNING",
        "ERROR",
        "NOT_CONFIGURED",
        "OPTIONAL",
        "REPAIRABLE",
    }


def test_product_doctor_is_offline_and_covers_product_subsystems(tmp_path):
    report = run_doctor(_config(tmp_path), registry_path=tmp_path / "daemons.json")
    checks = {item.name: item for item in report.checks}

    assert report.network_used is False
    assert checks["provider:deterministic"].status == CheckStatus.OK
    assert checks["configuration"].status == CheckStatus.OK
    assert checks["policy"].status == CheckStatus.OK
    assert checks["tool-runtime"].status == CheckStatus.OK
    assert checks["mcp"].status == CheckStatus.NOT_CONFIGURED
    assert checks["migration:execution-state"].status == CheckStatus.NOT_CONFIGURED
    assert checks["daemon-registry"].status == CheckStatus.NOT_CONFIGURED
    assert checks["repository-index"].status == CheckStatus.NOT_CONFIGURED
    assert checks["trace-storage"].status == CheckStatus.NOT_CONFIGURED
    assert "storage-size" in checks
    assert "vscode-extension" in checks


def test_doctor_repair_only_creates_safe_runtime_directories(tmp_path):
    config = _config(tmp_path)

    before = run_doctor(config, registry_path=tmp_path / "daemons.json")
    repaired = run_doctor(
        config,
        repair=True,
        registry_path=tmp_path / "daemons.json",
    )

    assert next(item for item in before.checks if item.name == "runtime-directories").status == CheckStatus.REPAIRABLE
    repair_check = next(
        item for item in repaired.checks if item.name == "runtime-directories"
    )
    assert repair_check.status == CheckStatus.OK
    assert (tmp_path / "runs").is_dir()
    assert (tmp_path / "state").is_dir()
    assert not (tmp_path / "daemons.json").exists()


def test_verbose_doctor_renders_safe_details(tmp_path):
    report = run_doctor(_config(tmp_path), registry_path=tmp_path / "daemons.json")

    rendered = render_doctor(report, verbose=True)

    assert "policy_mode: enforce" in rendered
    assert "Network used: no" in rendered


def test_doctor_cli_provider_selection_remains_offline(tmp_path, capsys):
    config = _config(tmp_path)
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[agentbus]\n"
        f"provider_name = 'deterministic'\n"
        f"workspace_dir = {json.dumps(config.workspace_dir)}\n"
        f"state_dir = {json.dumps(config.state_dir)}\n"
        f"runs_dir = {json.dumps(config.runs_dir)}\n",
        encoding="utf-8",
    )

    assert cli.main(
        [
            "doctor",
            "--config",
            str(config_file),
            "--provider",
            "deterministic",
            "--registry-path",
            str(tmp_path / "daemons.json"),
            "--json",
        ]
    ) in {0, 1}
    payload = json.loads(capsys.readouterr().out)

    assert payload["network_used"] is False
    selected = next(item for item in payload["checks"] if item["name"] == "provider:selected")
    assert selected["status"] == "OK"
