from __future__ import annotations

import json

from agentbus.cli import main


def test_repair_cli_creates_an_owned_case_and_previews_it(tmp_path, capsys):
    args = ["--workspace", str(tmp_path), "--json"]
    assert main(["repair", "demo", *args]) == 0
    case = json.loads(capsys.readouterr().out)
    assert case["summary"]["mode"] == "local_demo"
    case_id = case["summary"]["case_id"]
    assert main(["repair", "preview", case_id, *args]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["counts"]["repairable"] == 65
    assert main(["repair", "export", case_id, "--preview-id", preview["preview_id"], *args]) == 0
    assert json.loads(capsys.readouterr().out)["format"] == "syndra.repair-package/v1"
    assert (tmp_path / ".syndra" / "customer-repair" / "customer-repair.db").exists()


def test_repair_cli_rejects_private_input_without_echoing_content(tmp_path, capsys):
    assert main(["repair", "import", str(tmp_path / "unreadable-sensitive-name.json"), "--workspace", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "sensitive-name" not in captured.err
    assert json.loads(captured.err)["error"]["code"] == "invalid_repair_input"


def test_repair_cli_respects_discovered_workspace_storage(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    runtime = tmp_path / "configured-runtime"
    settings = workspace / ".syndra"
    settings.mkdir(parents=True)
    (settings / "config.toml").write_text("state_dir = " + json.dumps(str(runtime)) + "\n", encoding="utf-8")
    assert main(["repair", "demo", "--workspace", str(workspace), "--json"]) == 0
    capsys.readouterr()
    assert (runtime / "customer-repair" / "customer-repair.db").exists()
    assert not (settings / "customer-repair").exists()


def test_repair_cli_uses_explicit_configuration_without_defaulting_to_source_repo(tmp_path, capsys):
    workspace = tmp_path / "target"
    workspace.mkdir()
    runtime = tmp_path / "external"
    config = tmp_path / "repair-config.json"
    config.write_text(json.dumps({"workspace_dir": str(workspace), "state_dir": str(runtime)}), encoding="utf-8")
    assert main(["repair", "demo", "--config", str(config), "--json"]) == 0
    capsys.readouterr()
    assert (runtime / "customer-repair" / "customer-repair.db").exists()
    assert not (workspace / ".syndra").exists()
