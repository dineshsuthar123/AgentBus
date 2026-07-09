import sys

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
