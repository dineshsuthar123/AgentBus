from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agentbus.git.repository import GitRepository, GitRepositoryError
from agentbus.tools.git_tools import GitToolAuthorizationError, GitTools


def run_git(workspace: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return result.stdout.strip()


def initialized_repository(path: Path, *, include_protected: bool = False) -> Path:
    path.mkdir()
    run_git(path, "init", "-q")
    run_git(path, "config", "user.name", "AgentBus Test")
    run_git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "README.md").write_text("baseline\n", encoding="utf-8")
    run_git(path, "add", "README.md")
    if include_protected:
        (path / ".env").write_text(
            "API_KEY=must-not-reach-tool-output\n",
            encoding="utf-8",
        )
        run_git(path, "add", ".env")
    run_git(path, "commit", "-q", "-m", "chore: baseline")
    return path.resolve()


def test_read_only_git_tools_are_bounded_redacted_and_repository_scoped(
    tmp_path: Path,
) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    (workspace / "module.py").write_text(
        "API_KEY=must-not-reach-tool-output\nvalue = 1\n",
        encoding="utf-8",
    )
    tools = GitTools(str(workspace), max_diff_chars=2_000)
    repository_diff = GitRepository(str(workspace)).review_diff()

    assert "module.py" in tools.status()
    diff = tools.diff()
    assert "module.py" in diff
    assert "must-not-reach-tool-output" not in diff
    assert "[REDACTED]" in diff
    assert "must-not-reach-tool-output" not in repository_diff
    assert "[REDACTED]" in repository_diff
    assert "chore: baseline" in tools.log(maximum_entries=1)
    assert "README.md" in tools.show("HEAD")
    assert run_git(workspace, "branch", "--show-current") in tools.branches()
    assert len(tools.diff(max_chars=40)) <= 40


def test_committed_diff_redacts_secret_shaped_source_content(tmp_path: Path) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    base_commit = run_git(workspace, "rev-parse", "HEAD")
    (workspace / "module.py").write_text(
        "TOKEN=must-not-reach-reviewer\nvalue = 1\n",
        encoding="utf-8",
    )
    run_git(workspace, "add", "module.py")
    run_git(workspace, "commit", "-q", "-m", "feat: add module")

    diff = GitRepository(str(workspace)).commit_diff(base_commit)

    assert "must-not-reach-reviewer" not in diff
    assert "TOKEN=[REDACTED]" in diff


def test_show_omits_protected_files_from_historical_commit(tmp_path: Path) -> None:
    workspace = initialized_repository(
        tmp_path / "repository",
        include_protected=True,
    )

    output = GitTools(str(workspace)).show("HEAD")

    assert "README.md" in output
    assert ".env" not in output
    assert "must-not-reach-tool-output" not in output


def test_git_mutation_requires_owned_worktree(tmp_path: Path) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    (workspace / "module.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(GitToolAuthorizationError, match="owned"):
        GitTools(str(workspace)).stage(
            ["module.py"],
            task_id="task-1",
            invocation_id="invocation-1",
        )


def test_owned_worktree_stage_and_commit_are_attributed(tmp_path: Path) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    (workspace / "module.py").write_text("value = 1\n", encoding="utf-8")
    tools = GitTools(str(workspace), owned_worktree=True)

    staged = tools.stage(
        ["module.py"],
        task_id="task-1",
        invocation_id="invocation-stage",
    )
    committed = tools.commit(
        "feat: add module",
        ["module.py"],
        task_id="task-1",
        invocation_id="invocation-commit",
    )

    assert staged.paths == ("module.py",)
    assert staged.task_id == "task-1"
    assert committed.paths == ("module.py",)
    assert committed.task_id == "task-1"
    assert committed.invocation_id == "invocation-commit"
    assert committed.parent_commit != committed.commit
    assert len(committed.message_sha256) == 64
    assert run_git(workspace, "show", "--format=", "--name-only", "HEAD") == "module.py"
    with pytest.raises(FrozenInstanceError):
        committed.commit = "changed"  # type: ignore[misc]


def test_stage_rejects_protected_generated_and_option_paths(tmp_path: Path) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    (workspace / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (workspace / "build").mkdir()
    (workspace / "build" / "output.txt").write_text("generated\n", encoding="utf-8")
    tools = GitTools(str(workspace), owned_worktree=True)

    for index, path in enumerate((".env", "build/output.txt", "-n"), start=1):
        with pytest.raises(GitRepositoryError):
            tools.stage(
                [path],
                task_id="task-1",
                invocation_id=f"invocation-rejected-{index}",
            )
    with pytest.raises(ValueError, match="256"):
        tools.stage(
            [f"path-{index}.txt" for index in range(257)],
            task_id="task-1",
            invocation_id="invocation-many-paths",
        )
    with pytest.raises(TypeError, match="collection"):
        tools.stage(
            "module.py",
            task_id="task-1",
            invocation_id="invocation-string-paths",
        )
    with pytest.raises(ValueError, match="identifier"):
        tools.stage(
            ["module.py"],
            task_id="task/unsafe",
            invocation_id="invocation-invalid-id",
        )


def test_git_tool_surface_exposes_no_destructive_or_remote_operations(
    tmp_path: Path,
) -> None:
    tools = GitTools(str(initialized_repository(tmp_path / "repository")))

    for operation in (
        "push",
        "reset",
        "clean",
        "configure",
        "delete_branch",
        "run",
    ):
        assert hasattr(tools, operation) is False
