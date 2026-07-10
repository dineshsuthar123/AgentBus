import sys

import pytest

from agentbus import main as main_module
from agentbus.config import AgentBusConfig
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.models import (
    ExecutionReport,
    GraphProgress,
    RunStatus,
)
from agentbus.execution.state_store import StateStore


def config(tmp_path):
    return AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
    )


def simple_plan(risk="low"):
    return {
        "goal": "CLI task",
        "steps": [
            {
                "id": "step-1",
                "title": "Implement",
                "description": "Implement task",
                "risk": risk,
            }
        ],
        "test_strategy": "Run tests",
        "done_criteria": ["Done"],
    }


def create_run(settings, *, run_id="run-1", risk="low"):
    store = StateStore(settings.state_database_path)
    engine = DurableExecutionEngine(store)
    engine.create_run(
        "CLI task",
        simple_plan(risk),
        model="fake-model",
        workspace=settings.workspace_dir,
        run_id=run_id,
    )
    return store, engine


def patch_config(monkeypatch, settings):
    monkeypatch.setattr(main_module.AgentBusConfig, "from_env", lambda: settings)


def test_cli_accepts_durable_new_run_and_prints_id_first(monkeypatch, capsys, tmp_path):
    settings = config(tmp_path)
    patch_config(monkeypatch, settings)

    class FakeOrchestrator:
        def __init__(self, config, **kwargs):
            pass

        def create_durable_run(self, task):
            return "durable-123"

        def run_durable(self, run_id):
            return ExecutionReport(
                run_id=run_id,
                original_task="CLI task",
                status=RunStatus.SUCCEEDED,
                graph_progress=GraphProgress(
                    total=1,
                    succeeded=1,
                    failed=0,
                    blocked=0,
                    waiting_for_approval=0,
                    remaining=0,
                ),
            )

    monkeypatch.setattr(main_module, "MultiAgentOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(
        sys,
        "argv",
        ["agentbus.main", "--workflow", "multi", "--durable", "CLI task"],
    )

    assert main_module.main() == 0
    output = capsys.readouterr().out
    assert output.index("Run ID: durable-123") < output.index("Status: succeeded")


def test_cli_list_runs_does_not_prompt_or_execute(monkeypatch, capsys, tmp_path):
    settings = config(tmp_path)
    create_run(settings)
    patch_config(monkeypatch, settings)
    monkeypatch.setattr(
        "builtins.input", lambda prompt: pytest.fail("list must not prompt")
    )
    monkeypatch.setattr(sys, "argv", ["agentbus.main", "--list-runs"])

    assert main_module.main() == 0
    output = capsys.readouterr().out
    assert "run-1" in output
    assert "pending" in output


def test_cli_show_run_is_read_only_and_prints_status(monkeypatch, capsys, tmp_path):
    settings = config(tmp_path)
    create_run(settings)
    patch_config(monkeypatch, settings)

    class MustNotConstruct:
        def __init__(self, *args, **kwargs):
            pytest.fail("show-run must not construct the orchestrator")

    monkeypatch.setattr(main_module, "MultiAgentOrchestrator", MustNotConstruct)
    monkeypatch.setattr(
        sys, "argv", ["agentbus.main", "--show-run", "run-1"]
    )

    assert main_module.main() == 0
    output = capsys.readouterr().out
    assert "Status: pending" in output
    assert "Tasks: 0/1 succeeded" in output


def test_cli_resume_uses_persisted_workspace_without_prompt(monkeypatch, capsys, tmp_path):
    settings = config(tmp_path)
    create_run(settings)
    patch_config(monkeypatch, settings)
    seen = {}

    class FakeOrchestrator:
        def __init__(self, config, state_store):
            seen["workspace"] = config.workspace_dir
            self.store = state_store

        def resume_durable(self, run_id):
            return DurableExecutionEngine(self.store).get_report(run_id)

    monkeypatch.setattr(main_module, "MultiAgentOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(
        "builtins.input", lambda prompt: pytest.fail("resume must not prompt")
    )
    monkeypatch.setattr(sys, "argv", ["agentbus.main", "--resume", "run-1"])

    assert main_module.main() == 0
    assert seen["workspace"] == settings.workspace_dir
    assert "Status: pending" in capsys.readouterr().out


def test_cli_approve_and_reject_persist_decisions(monkeypatch, capsys, tmp_path):
    settings = config(tmp_path)
    store, engine = create_run(settings, run_id="approve-run", risk="high")
    engine.run_until_blocked("approve-run")
    patch_config(monkeypatch, settings)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agentbus.main",
            "--approve",
            "approve-run:step-1",
            "--reason",
            "Reviewed",
        ],
    )

    assert main_module.main() == 0
    assert "Status: running" in capsys.readouterr().out
    assert store.latest_approval("approve-run", "step-1").reason == "Reviewed"

    _, reject_engine = create_run(settings, run_id="reject-run", risk="high")
    reject_engine.run_until_blocked("reject-run")
    monkeypatch.setattr(
        sys,
        "argv",
        ["agentbus.main", "--reject", "reject-run:step-1", "--reason", "Unsafe"],
    )

    assert main_module.main() == 1
    assert "Status: failed" in capsys.readouterr().out


def test_cli_cancel_run_prevents_resume(monkeypatch, capsys, tmp_path):
    settings = config(tmp_path)
    store, _ = create_run(settings)
    patch_config(monkeypatch, settings)
    monkeypatch.setattr(
        sys,
        "argv",
        ["agentbus.main", "--cancel-run", "run-1", "--reason", "Stop"],
    )

    assert main_module.main() == 0
    assert store.get_run("run-1").status == RunStatus.CANCELLED
    assert "Status: cancelled" in capsys.readouterr().out


def test_cli_rejects_conflicting_operations(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["agentbus.main", "--resume", "run-1", "--list-runs"],
    )

    with pytest.raises(SystemExit) as captured:
        main_module.parse_args()

    assert captured.value.code == 2

def test_cli_rejects_durable_single_workflow(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["agentbus.main", "--durable", "Task"],
    )

    with pytest.raises(SystemExit) as captured:
        main_module.parse_args()

    assert captured.value.code == 2
