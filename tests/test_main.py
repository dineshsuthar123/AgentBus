import sys
from types import SimpleNamespace

from agentbus import main as main_module


class FakeLoop:
    seen_configs = []
    seen_tasks = []

    def __init__(self, config):
        self.config = config
        self.seen_configs.append(config)

    def run(self, task):
        self.seen_tasks.append(task)
        return "fake result"


def test_cli_uses_command_line_task(monkeypatch, capsys):
    FakeLoop.seen_configs = []
    FakeLoop.seen_tasks = []
    monkeypatch.setattr(main_module, "AgentLoop", FakeLoop)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agentbus.main",
            "Create hello.py",
            "--model",
            "test-model",
            "--workspace",
            "test-workspace",
            "--max-steps",
            "2",
        ],
    )

    main_module.main()

    output = capsys.readouterr().out
    config = FakeLoop.seen_configs[0]

    assert "Task: Create hello.py" in output
    assert "Workspace: test-workspace" in output
    assert "Model: test-model" in output
    assert "fake result" in output
    assert config.max_steps == 2
    assert FakeLoop.seen_tasks == ["Create hello.py"]


def test_cli_prompts_when_no_task(monkeypatch, capsys):
    FakeLoop.seen_configs = []
    FakeLoop.seen_tasks = []
    monkeypatch.setattr(main_module, "AgentLoop", FakeLoop)
    monkeypatch.setattr(sys, "argv", ["agentbus.main"])
    monkeypatch.setattr("builtins.input", lambda prompt: "Interactive task")

    main_module.main()

    output = capsys.readouterr().out

    assert "Task: Interactive task" in output
    assert "fake result" in output
    assert FakeLoop.seen_tasks == ["Interactive task"]


def test_cli_accepts_multi_workflow(monkeypatch, capsys):
    seen_configs = []
    seen_tasks = []

    class FakeOrchestrator:
        def __init__(self, config):
            seen_configs.append(config)

        def run(self, task):
            seen_tasks.append(task)
            return SimpleNamespace(
                planner_summary="Create calculator (1 steps)",
                verifier_result={
                    "command": ["python", "-m", "pytest"],
                    "passed": True,
                },
                approved=True,
                final_summary="multi done",
            )

    monkeypatch.setattr(main_module, "MultiAgentOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agentbus.main",
            "--workflow",
            "multi",
            "Create calculator",
        ],
    )

    main_module.main()

    output = capsys.readouterr().out

    assert "Workflow: multi" in output
    assert "Planner: Create calculator (1 steps)" in output
    assert "Verifier: passed" in output
    assert "Reviewer approved: True" in output
    assert "multi done" in output
    assert seen_tasks == ["Create calculator"]
    assert seen_configs[0].workspace_dir == "workspace"
