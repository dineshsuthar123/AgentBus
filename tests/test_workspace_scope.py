from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agentbus.agents.coder import CoderAgent
from agentbus.config import AgentBusConfig
from agentbus.execution.models import RunRecord, TaskExecutionContext, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import (
    GitRepository,
    GitRepositoryError,
    WorkspaceRepositoryMismatch,
)
from agentbus.runtime.durable_workflow import MultiAgentTaskExecutor
from agentbus.runtime.loop import AgentLoop
from agentbus.runtime.orchestrator import MultiAgentOrchestrator
from agentbus.tools.git_tools import GitTools


def init_repository(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        ["git", "init", "-q"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )

    subprocess.run(
        ["git", "config", "user.name", "AgentBus Test"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )

    subprocess.run(
        ["git", "config", "user.email", "agentbus@example.invalid"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )

    return path.resolve()


def run_git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "AgentBus Test",
            "GIT_AUTHOR_EMAIL": "agentbus@example.invalid",
            "GIT_COMMITTER_NAME": "AgentBus Test",
            "GIT_COMMITTER_EMAIL": "agentbus@example.invalid",
        },
    )
    return result.stdout


def test_nested_directory_in_parent_repository_is_rejected(tmp_path):
    parent = init_repository(tmp_path / "parent")
    workspace = parent / "nested-workspace"
    workspace.mkdir()

    repository = GitRepository(str(workspace))

    with pytest.raises(WorkspaceRepositoryMismatch) as captured:
        repository.changed_files()

    message = str(captured.value)
    assert str(workspace.resolve()) in message
    assert str(parent) in message
    assert "parent repository" in message


def test_isolated_workspace_repository_is_accepted(tmp_path):
    workspace = init_repository(tmp_path / "isolated-workspace")

    repository = GitRepository(str(workspace))

    assert repository.validate_workspace() == workspace
    assert repository.is_git_repo() is True


def test_git_diff_and_changed_files_are_scoped_to_workspace(tmp_path):
    parent = init_repository(tmp_path / "parent")
    (parent / "unrelated-parent.txt").write_text("parent only\n", encoding="utf-8")
    workspace = init_repository(parent / "target")
    (workspace / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    repository = GitRepository(str(workspace))
    changed = repository.changed_files()
    diff = repository.full_diff()

    assert changed == ["calculator.py"]
    assert "calculator.py" in diff
    assert "unrelated-parent.txt" not in diff


def test_git_diffs_and_commits_exclude_protected_workspace_files(tmp_path):
    workspace = init_repository(tmp_path / "target")
    (workspace / "module.py").write_text("value = 1\n", encoding="utf-8")
    (workspace / ".env").write_text(
        "API_KEY=must-not-reach-review\n",
        encoding="utf-8",
    )

    repository = GitRepository(str(workspace))
    changes = repository.change_set()
    diff = repository.full_diff()

    assert changes.protected_files == [".env"]
    assert ".env" in changes.changed_files
    assert ".env" in changes.review_excluded_files
    assert ".env" not in changes.review_files
    assert ".env" not in changes.commit_files
    assert "must-not-reach-review" not in diff
    assert "module.py" in diff
    with pytest.raises(GitRepositoryError, match="Protected"):
        repository.full_diff(paths=[".env"])


def test_path_scoped_commit_does_not_include_unrelated_staged_changes(
    tmp_path, monkeypatch
):
    isolated_home = tmp_path / "git-home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("USERPROFILE", str(isolated_home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)

    workspace = init_repository(tmp_path / "target")
    assert run_git(workspace, "config", "--local", "user.name").strip() == "AgentBus Test"
    assert (
        run_git(workspace, "config", "--local", "user.email").strip()
        == "agentbus@example.invalid"
    )
    (workspace / "unrelated.txt").write_text("baseline\n", encoding="utf-8")
    run_git(workspace, "add", "unrelated.txt")
    run_git(workspace, "commit", "-m", "baseline")
    (workspace / "unrelated.txt").write_text("user staged work\n", encoding="utf-8")
    run_git(workspace, "add", "unrelated.txt")
    (workspace / "calculator.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

    repository = GitRepository(str(workspace))
    repository.commit("fix: calculator", paths=["calculator.py"])

    committed_files = run_git(workspace, "show", "--pretty=format:", "--name-only", "HEAD")
    assert committed_files.strip() == "calculator.py"
    assert run_git(workspace, "diff", "--cached", "--name-only").strip() == "unrelated.txt"


def test_unrelated_parent_files_never_reach_task_reviewer(tmp_path):
    parent = init_repository(tmp_path / "parent")
    (parent / "unrelated-parent.txt").write_text("do not review\n", encoding="utf-8")
    workspace = init_repository(parent / "target")

    class Coder:
        def execute(self, user_task, plan, reviewer_feedback=None):
            (workspace / "calculator.py").write_text(
                "def add(a, b):\n    return a + b\n",
                encoding="utf-8",
            )
            return "implemented current task"

    class Verifier:
        def verify(self):
            return {
                "passed": True,
                "command": ["python", "-m", "pytest"],
                "exit_code": 0,
                "output": "passed",
                "reason": "fake",
            }

    class Reviewer:
        def __init__(self):
            self.task_input = None

        def review_task(self, **kwargs):
            self.task_input = kwargs
            return {
                "approved": True,
                "issues": [],
                "summary": "task approved",
                "required_fixes": [],
            }

    reviewer = Reviewer()
    repository = GitRepository(str(workspace))
    executor = MultiAgentTaskExecutor(
        coder=Coder(),
        verifier=Verifier(),
        reviewer=reviewer,
        git_tools=GitTools(str(workspace)),
        git_repository=repository,
    )
    task = TaskSpec(
        task_id="step-1",
        title="Implement calculator",
        description="Create calculator.py",
        expected_outputs=["calculator.py"],
        done_criteria=["Calculator implementation exists"],
    )
    context = TaskExecutionContext(
        run=RunRecord(
            run_id="scope-run",
            original_task="Build a calculator",
            model="fake",
            workspace=str(workspace),
        ),
        task=task,
        attempt_number=1,
    )

    result = executor.execute(context)

    assert result.succeeded is True
    assert result.changed_files == ["calculator.py"]
    assert reviewer.task_input["artifacts"] == ["calculator.py"]
    assert "unrelated-parent.txt" not in reviewer.task_input["task_diff"]


def test_explicit_absolute_workspace_propagates_to_runtime_components(tmp_path):
    workspace = init_repository(tmp_path / "target")
    settings = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
    )

    class Planner:
        def plan(self, user_task, file_list=None, context_pack=None):
            return {
                "goal": "No-op",
                "steps": [
                    {
                        "id": "step-1",
                        "title": "No-op",
                        "description": "Finish safely",
                        "risk": "low",
                    }
                ],
                "test_strategy": "Fake",
                "done_criteria": ["Done"],
            }

    class Model:
        def generate_json(self, prompt, **kwargs):
            return {"action": "finish", "summary": "workspace checked"}

    coder = CoderAgent(config=settings, model=Model())
    store = StateStore(settings.state_database_path)
    runner = MultiAgentOrchestrator(
        config=settings,
        planner=Planner(),
        coder=coder,
        state_store=store,
    )
    loop = AgentLoop(config=settings, model=Model())
    loop.run("Initialize managed tools")
    run_id = runner.create_durable_run("Check workspace propagation")
    executor = runner._durable_engine(run_id).task_executor

    assert settings.workspace_path == workspace
    assert runner.workspace == workspace
    assert runner.scanner.workspace == workspace
    assert runner.test_detector.workspace == workspace
    assert runner.context_builder.workspace == workspace
    assert runner.verifier.workspace == workspace
    assert runner.verifier.command_tools.workspace == workspace
    assert runner.git.workspace == workspace
    assert runner.git_repository.workspace == workspace
    assert runner.pr_client.workspace == workspace
    assert coder.config.workspace_path == workspace
    assert loop.tool_runtime is not None
    assert loop.tool_runtime.workspace == workspace
    assert loop.tool_runtime.worktree == workspace
    assert executor.git_repository.workspace == workspace
    assert executor.workspace == workspace
    assert executor.tool_runtime is not None
    assert executor.tool_runtime.workspace == workspace
    assert executor.tool_runtime.worktree == workspace
    assert store.get_run(run_id).workspace == str(workspace)


def test_parallel_worker_runtime_propagates_isolated_absolute_workspace(tmp_path):
    source = init_repository(tmp_path / "source")
    worker_workspace = (tmp_path / "worktrees" / "task-A").resolve()
    worker_workspace.mkdir(parents=True)
    settings = AgentBusConfig(
        workspace_dir=str(source),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        parallel_execution=True,
        worktree_root=str(tmp_path / "worktrees"),
    )
    runner = MultiAgentOrchestrator(config=settings)

    executor = runner._parallel_task_executor(worker_workspace)
    class Model:
        def generate_json(self, prompt, **kwargs):
            return {"action": "finish", "summary": "workspace checked"}

    loop = AgentLoop(config=executor.coder.config, model=Model())
    loop.run("Initialize managed tools")

    assert executor.workspace == worker_workspace
    assert executor.git_repository.workspace == worker_workspace
    assert executor.git_tools.workspace == worker_workspace
    assert executor.coder.config.workspace_path == worker_workspace
    assert executor.reviewer.config.workspace_path == worker_workspace
    assert executor.verifier.workspace == worker_workspace
    assert executor.verifier.command_tools.workspace == worker_workspace
    assert executor.verifier.test_detector.workspace == worker_workspace
    assert executor.tool_runtime is not None
    assert executor.tool_runtime.workspace == source
    assert executor.tool_runtime.worktree == worker_workspace
    assert Path(loop.workspace) == worker_workspace
    assert loop.tool_runtime is not None
    assert loop.tool_runtime.workspace == worker_workspace
    assert loop.tool_runtime.worktree == worker_workspace
