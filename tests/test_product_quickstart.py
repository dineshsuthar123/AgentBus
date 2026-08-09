from __future__ import annotations

import json
from pathlib import Path

from agentbus.cli import main
from agentbus.product import quickstart


def test_quickstart_runs_complete_offline_product_path(tmp_path):
    result = quickstart.run_quickstart(keep_demo=True, temp_parent=tmp_path)

    assert result.ok is True
    assert result.provider == "deterministic"
    assert result.workspace is not None and result.workspace.is_dir()
    assert result.kept_demo is True
    assert result.cleaned is False
    assert result.indexed_files >= 1
    assert result.planner_steps == 1
    assert result.verifier_passed is True
    assert result.reviewer_approved is True
    assert result.changed_files == (
        "agentbus_result.py",
        "test_agentbus_result.py",
    )
    assert (result.workspace / "agentbus_result.py").is_file()
    assert (result.workspace / "test_agentbus_result.py").is_file()
    assert result.to_dict()["network_used"] is False
    statuses = {step.name: step.status for step in result.steps}
    assert statuses == {
        "environment": "passed",
        "demo_repository": "passed",
        "repository_index": "passed",
        "managed_task": "passed",
        "planner": "passed",
        "managed_filesystem": "passed",
        "tests": "passed",
        "verification": "passed",
        "review": "passed",
        "changes": "passed",
        "report": "passed",
        "cleanup": "skipped",
    }


def test_quickstart_removes_only_its_owned_temporary_container(tmp_path):
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("preserve me\n", encoding="utf-8")

    result = quickstart.run_quickstart(temp_parent=tmp_path)

    assert result.ok is True
    assert result.cleaned is True
    assert result.workspace is not None and not result.workspace.exists()
    assert unrelated.read_text(encoding="utf-8") == "preserve me\n"
    assert list(tmp_path.glob("agentbus-quickstart-*")) == []


def test_quickstart_failure_is_sanitized_and_cleans_owned_state(tmp_path, monkeypatch):
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("preserve me\n", encoding="utf-8")

    def fail_index(_workspace: Path, _database: Path):
        raise RuntimeError("index failed with bearer secret-value")

    monkeypatch.setattr(quickstart, "_index_repository", fail_index)

    result = quickstart.run_quickstart(temp_parent=tmp_path)

    assert result.ok is False
    assert result.cleaned is True
    assert result.error is not None
    assert result.error["category"] == "INDEX_ERROR"
    assert "secret-value" not in str(result.to_dict())
    assert unrelated.read_text(encoding="utf-8") == "preserve me\n"
    assert list(tmp_path.glob("agentbus-quickstart-*")) == []


def test_quickstart_cli_json_reports_success_and_cleanup(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(quickstart.tempfile, "gettempdir", lambda: str(tmp_path))

    exit_code = main(["quickstart", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["provider"] == "deterministic"
    assert payload["cleaned"] is True
    assert payload["network_used"] is False
    assert payload["changed_files"] == [
        "agentbus_result.py",
        "test_agentbus_result.py",
    ]
    assert list(tmp_path.glob("agentbus-quickstart-*")) == []
