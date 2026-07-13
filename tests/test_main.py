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
    monkeypatch.setenv("AGENTBUS_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_OPENAI_DEFAULT_DEPLOYMENT", "agentbus-main")
    monkeypatch.setenv("AZURE_OPENAI_CODER_DEPLOYMENT", "agentbus-main")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "cli-secret-must-not-appear")
    monkeypatch.setattr(main_module, "AgentLoop", FakeLoop)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agentbus.main",
            "Create hello.py",
            "--provider",
            "ollama",
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
    assert "Provider: ollama" in output
    assert "Model: test-model" in output
    assert "agentbus-main" not in output
    assert "cli-secret-must-not-appear" not in output
    assert "fake result" in output
    assert config.provider_name == "ollama"
    assert config.resolve_model("coder") == "test-model"
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
        seen_kwargs = []

        def __init__(self, config, **kwargs):
            seen_configs.append(config)
            self.seen_kwargs.append(kwargs)

        def run(self, task):
            seen_tasks.append(task)
            return SimpleNamespace(
                planner_summary="Create calculator (1 steps)",
                verifier_result={
                    "command": ["python", "-m", "pytest"],
                    "passed": True,
                },
                git_branch=None,
                changed_files=[],
                approved=True,
                commit_hash=None,
                pr_url=None,
                pr_error=None,
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


def test_cli_show_context_prints_and_exits_without_task(monkeypatch, capsys):
    FakeLoop.seen_configs = []
    FakeLoop.seen_tasks = []
    monkeypatch.setattr(main_module, "AgentLoop", FakeLoop)
    monkeypatch.setattr(main_module, "build_context_pack", lambda config, task=None: "CTX")
    monkeypatch.setattr(sys, "argv", ["agentbus.main", "--show-context"])

    main_module.main()

    output = capsys.readouterr().out

    assert "CTX" in output
    assert "Task:" not in output
    assert FakeLoop.seen_tasks == []


def test_cli_parses_git_workflow_flags(monkeypatch, capsys):
    seen = {}

    class FakeOrchestrator:
        def __init__(self, config, **kwargs):
            seen["config"] = config
            seen["kwargs"] = kwargs

        def run(self, task):
            seen["task"] = task
            return SimpleNamespace(
                planner_summary="Plan (1 steps)",
                git_branch="agentbus/add-tests",
                changed_files=["tests/test_calculator.py"],
                verifier_result={
                    "command": ["python", "-m", "pytest"],
                    "passed": True,
                },
                approved=True,
                commit_hash="abc1234",
                pr_url="https://github.com/acme/repo/pull/1",
                pr_error=None,
                final_summary="done",
            )

    monkeypatch.setattr(main_module, "MultiAgentOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agentbus.main",
            "--workflow",
            "multi",
            "--create-branch",
            "--branch-name",
            "agentbus/add-tests",
            "--commit",
            "--open-pr",
            "--pr-base",
            "develop",
            "Add calculator tests",
        ],
    )

    main_module.main()

    output = capsys.readouterr().out

    assert seen["task"] == "Add calculator tests"
    assert seen["kwargs"]["create_branch"] is True
    assert seen["kwargs"]["branch_name"] == "agentbus/add-tests"
    assert seen["kwargs"]["commit_changes"] is True
    assert seen["kwargs"]["open_pr"] is True
    assert seen["kwargs"]["pr_base"] == "develop"
    assert "Commit: abc1234" in output
    assert "PR: https://github.com/acme/repo/pull/1" in output
