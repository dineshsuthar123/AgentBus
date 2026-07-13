import subprocess

import pytest

from agentbus.git.branching import generate_branch_name
from agentbus.git.commit_message import generate_commit_message
from agentbus.git.repository import GitRepository
from agentbus.github.pr import GitHubPullRequestClient
from agentbus.github.pr_body import build_pr_body


def test_generate_branch_name_is_safe_and_prefixed():
    branch = generate_branch_name("Add calculator tests!!!", prefix="agentbus")

    assert branch.startswith("agentbus/add-calculator-tests-")
    assert branch == branch.lower()
    assert "!" not in branch
    assert len(branch) <= 60


def test_generate_commit_message_uses_conventional_prefixes():
    assert generate_commit_message("Fix invalid model output", ["agentbus/x.py"]).startswith(
        "fix:"
    )
    assert generate_commit_message("Update docs", ["README.md"]).startswith("docs:")
    assert generate_commit_message("Add tests", ["tests/test_x.py"]).startswith("test:")
    assert generate_commit_message("Add calculator", ["calculator.py"]).startswith("feat:")


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_git_repository_uses_safe_command_construction(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, cwd, capture_output, text, timeout, shell):
        commands.append(command)
        assert cwd == tmp_path
        assert capture_output is True
        assert text is True
        assert shell is False
        return FakeCompleted(stdout=f"{tmp_path}\n")

    monkeypatch.setattr("agentbus.git.repository.subprocess.run", fake_run)

    repo = GitRepository(str(tmp_path))

    assert repo.is_git_repo() is True
    assert commands == [["git", "rev-parse", "--show-toplevel"]]


def test_git_repository_parses_changed_files(monkeypatch, tmp_path):
    repo = GitRepository(str(tmp_path))
    monkeypatch.setattr(
        repo,
        "_run",
        lambda command: (
            " M app.py\0?? tests/test_app.py\0R  new.py\0old.py\0"
        ),
    )

    assert repo.changed_files() == ["app.py", "new.py", "tests/test_app.py"]


def test_pr_body_builder_includes_required_sections():
    body = build_pr_body(
        user_task="Add calculator tests",
        planner_summary="Create calculator tests (1 steps)",
        verifier_result={
            "passed": True,
            "command": ["python", "-m", "pytest"],
            "reason": "Detected pytest",
        },
        reviewer_result={"approved": True, "summary": "Looks good"},
        changed_files=["calculator.py", "tests/test_calculator.py"],
        test_command=["python", "-m", "pytest"],
    )

    assert "Add calculator tests" in body
    assert "Create calculator tests" in body
    assert "`calculator.py`" in body
    assert "AgentBus does not force push" in body


def test_github_client_handles_missing_gh_cli(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr("agentbus.github.pr.subprocess.run", fake_run)

    result = GitHubPullRequestClient(str(tmp_path)).create_pr(
        title="Test PR",
        body="Body",
    )

    assert "GitHub CLI not found" in result
