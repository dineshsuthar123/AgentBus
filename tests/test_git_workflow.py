import os
import subprocess
from pathlib import Path

import pytest

from agentbus.git.branching import generate_branch_name
from agentbus.git.commit_message import generate_commit_message
from agentbus.git.repository import GitRepository, GitRepositoryError
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

    monkeypatch.setenv("AGENTBUS_API_KEY", "must-not-reach-git")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "attacker-controlled"))

    def fake_run(command, cwd, capture_output, text, timeout, shell, env):
        commands.append(command)
        assert cwd == tmp_path
        assert capture_output is True
        assert text is True
        assert shell is False
        assert Path(command[0]).is_absolute()
        assert "--no-pager" in command
        assert "--literal-pathspecs" in command
        assert f"core.hooksPath={os.devnull}" in command
        assert "--show-toplevel" in command
        assert "AGENTBUS_API_KEY" not in env
        assert "GIT_DIR" not in env
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        return FakeCompleted(stdout=f"{tmp_path}\n")

    monkeypatch.setattr("agentbus.git.repository.subprocess.run", fake_run)

    repo = GitRepository(str(tmp_path))

    assert repo.is_git_repo() is True
    assert len(commands) == 1


def test_git_repository_bounds_and_redacts_process_diagnostics(monkeypatch, tmp_path):
    def oversized_run(*args, **kwargs):
        return FakeCompleted(stdout="x" * 101)

    monkeypatch.setattr("agentbus.git.repository.subprocess.run", oversized_run)
    repository = GitRepository(str(tmp_path), maximum_command_output_chars=100)

    with pytest.raises(RuntimeError, match="bounded output"):
        repository._run_unvalidated(["git", "status"])

    def failed_run(*args, **kwargs):
        return FakeCompleted(stderr="API_KEY=must-not-leak", returncode=1)

    monkeypatch.setattr("agentbus.git.repository.subprocess.run", failed_run)
    with pytest.raises(RuntimeError) as captured:
        repository._run_unvalidated(["git", "status"])
    assert "must-not-leak" not in str(captured.value)
    assert "[REDACTED]" in str(captured.value)


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


def test_git_repository_accepts_porcelain_ignored_directory_marker(
    monkeypatch, tmp_path
):
    repository = GitRepository(str(tmp_path))
    monkeypatch.setattr(
        repository,
        "_run",
        lambda command: "?? app.py\0!! .pytest_cache/v/\0",
    )

    assert repository.changed_files() == ["app.py"]
    assert repository.ignored_files() == [".pytest_cache/v"]


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "-n",
        ":(glob)**",
        "name.txt:stream",
        "line\nbreak.txt",
    ],
)
def test_git_repository_rejects_unsafe_pathspecs(tmp_path, path):
    repository = GitRepository(str(tmp_path))

    with pytest.raises(GitRepositoryError):
        repository.full_diff(paths=[path])


@pytest.mark.parametrize(
    "revision",
    ["--output=outside", "HEAD..other", "HEAD@{1}", "HEAD^", "refs/*"],
)
def test_git_repository_rejects_revision_injection(tmp_path, revision):
    repository = GitRepository(str(tmp_path))

    with pytest.raises(GitRepositoryError, match="unsafe syntax"):
        repository.changed_files_between(revision)


def test_commit_message_is_bounded_and_single_line(tmp_path):
    repository = GitRepository(str(tmp_path))

    with pytest.raises(GitRepositoryError, match="single-line"):
        repository.commit("fix: first line\nsecond line", paths=["module.py"])
    with pytest.raises(GitRepositoryError, match="512"):
        repository.commit("x" * 513, paths=["module.py"])


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
