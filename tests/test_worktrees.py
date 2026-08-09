from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.integration import (
    IntegrationConflictError,
    IntegrationCoordinator,
)
from agentbus.execution.models import TaskStatus
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepository
from agentbus.worktrees.errors import (
    WorktreeDirtyError,
    WorktreeOwnershipError,
    WorktreeRemovalUnsafeError,
    WorktreeRepositoryMismatchError,
)
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.models import (
    IntegrationRecord,
    MergeStatus,
    TaskCommitRecord,
    WorktreeStatus,
)


PLAN = {
    "goal": "Parallel worktree test",
    "steps": [
        {
            "id": "task-a",
            "title": "Task A",
            "description": "A",
            "dependencies": [],
            "risk": "low",
        },
        {
            "id": "task-b",
            "title": "Task B",
            "description": "B",
            "dependencies": [],
            "risk": "low",
        },
    ],
    "test_strategy": "offline",
    "done_criteria": ["done"],
}


def git(path, *arguments):
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout.strip()


def repository(path):
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "AgentBus Test")
    git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "shared.txt").write_text("baseline\n", encoding="utf-8")
    git(path, "add", "shared.txt")
    git(path, "commit", "-q", "-m", "baseline")
    return path.resolve(), git(path, "rev-parse", "HEAD")


def state(path, workspace):
    store = StateStore(path)
    DurableExecutionEngine(store).create_run(
        "Parallel tasks",
        PLAN,
        model="fake",
        workspace=str(workspace),
        run_id="run-1",
    )
    return store


def test_task_and_integration_worktrees_use_exact_base_and_leave_source_unchanged(
    tmp_path,
):
    source, base = repository(tmp_path / "repo")
    store = state(tmp_path / "state.db", source)
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)

    integration = manager.create_integration_worktree("run-1", base)
    task = manager.create_task_worktree("run-1", "task-a", base, "worker-1")

    assert integration.base_commit == base
    assert task.base_commit == base
    assert GitRepository(integration.path).head_commit(short=False) == base
    assert GitRepository(task.path).head_commit(short=False) == base
    assert manager.validate(task) == manager.validate(task).resolve()
    assert git(source, "status", "--porcelain") == ""
    assert git(source, "rev-parse", "HEAD") == base


def test_wrong_repository_and_unknown_cleanup_are_rejected(tmp_path):
    source, base = repository(tmp_path / "repo")
    other, _ = repository(tmp_path / "other")
    store = state(tmp_path / "state.db", source)
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    record = manager.create_task_worktree("run-1", "task-a", base, "worker-1")

    with pytest.raises(WorktreeRepositoryMismatchError):
        manager.validate(record.model_copy(update={"repository_root": str(other)}))
    with pytest.raises(WorktreeOwnershipError):
        manager.remove("unknown-worktree")
    with pytest.raises(WorktreeRemovalUnsafeError):
        manager.remove(record.worktree_id)


def test_dirty_cleanup_refused_and_explicit_clean_cleanup_succeeds(tmp_path):
    source, base = repository(tmp_path / "repo")
    store = state(tmp_path / "state.db", source)
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    dirty = manager.create_task_worktree("run-1", "task-a", base, "worker-1")
    (manager.validate(dirty) / "dirty.txt").write_text("keep me\n", encoding="utf-8")
    assert manager.is_clean(dirty) is False
    manager.mark_cleanup_pending(dirty.worktree_id)

    with pytest.raises(WorktreeDirtyError):
        manager.remove(dirty.worktree_id)
    assert (manager.validate(dirty) / "dirty.txt").exists()

    clean = manager.create_task_worktree("run-1", "task-b", base, "worker-2")
    assert manager.is_clean(clean) is True
    manager.mark_cleanup_pending(clean.worktree_id)
    removed = manager.remove(clean.worktree_id)
    assert removed.status == WorktreeStatus.REMOVED
    assert not manager.validate(dirty).samefile(source)


def test_conflict_is_recorded_and_aborted_without_touching_source(tmp_path):
    source, base = repository(tmp_path / "repo")
    store = state(tmp_path / "state.db", source)
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    integration_worktree = manager.create_integration_worktree("run-1", base)
    task_records = []
    for task_id, content in (("task-a", "from A\n"), ("task-b", "from B\n")):
        worktree = manager.create_task_worktree(
            "run-1", task_id, base, f"worker-{task_id}"
        )
        path = manager.validate(worktree)
        (path / "shared.txt").write_text(content, encoding="utf-8")
        repo = GitRepository(str(path))
        parent = repo.head_commit(short=False)
        repo.commit(f"feat: {task_id}", paths=["shared.txt"])
        commit = TaskCommitRecord(
            run_id="run-1",
            task_id=task_id,
            commit_sha=repo.head_commit(short=False),
            parent_sha=parent,
            worktree_id=worktree.worktree_id,
            changed_files=["shared.txt"],
        )
        store.record_task_commit(commit)
        store.update_task_status("run-1", task_id, TaskStatus.READY)
        store.update_task_status("run-1", task_id, TaskStatus.RUNNING)
        store.update_task_status("run-1", task_id, TaskStatus.INTEGRATION_PENDING)
        task_records.append(commit)
    coordinator = IntegrationCoordinator(store, manager)

    first = coordinator.integrate(integration_worktree, task_records[0])
    with pytest.raises(IntegrationConflictError) as captured:
        coordinator.integrate(integration_worktree, task_records[1])

    assert first.status.value == "integrated"
    assert captured.value.conflict_files == ["shared.txt"]
    assert store.get_task("run-1", "task-b").status == TaskStatus.INTEGRATION_CONFLICT
    assert store.list_integrations("run-1")[-1].conflict_files == ["shared.txt"]
    assert (source / "shared.txt").read_text(encoding="utf-8") == "baseline\n"
    assert git(source, "status", "--porcelain") == ""
    assert git(source, "rev-parse", "HEAD") == base
    assert git(source, "cat-file", "-t", task_records[1].commit_sha) == "commit"
    integration_path = manager.validate(integration_worktree)
    assert not Path(git(integration_path, "rev-parse", "--git-path", "CHERRY_PICK_HEAD")).exists()


def test_completed_cherry_pick_is_reconciled_after_state_persistence_interruption(
    tmp_path,
):
    source, base = repository(tmp_path / "repo")
    store = state(tmp_path / "state.db", source)
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    integration_worktree = manager.create_integration_worktree("run-1", base)
    task_worktree = manager.create_task_worktree(
        "run-1", "task-a", base, "worker-task-a"
    )
    task_path = manager.validate(task_worktree)
    (task_path / "module_a.py").write_text("VALUE_A = 1\n", encoding="utf-8")
    task_repository = GitRepository(str(task_path))
    task_repository.commit("feat: task-a", paths=["module_a.py"])
    task_commit = TaskCommitRecord(
        run_id="run-1",
        task_id="task-a",
        commit_sha=task_repository.head_commit(short=False),
        parent_sha=base,
        worktree_id=task_worktree.worktree_id,
        changed_files=["module_a.py"],
    )
    store.record_task_commit(task_commit)
    store.update_task_status("run-1", "task-a", TaskStatus.READY)
    store.update_task_status("run-1", "task-a", TaskStatus.RUNNING)
    store.update_task_status("run-1", "task-a", TaskStatus.INTEGRATION_PENDING)
    attempt = store.record_integration(
        IntegrationRecord(
            integration_id=uuid.uuid4().hex,
            run_id="run-1",
            task_id="task-a",
            task_commit=task_commit.commit_sha,
            base_commit=base,
            status=MergeStatus.INTEGRATING,
        )
    )
    store.update_task_status("run-1", "task-a", TaskStatus.INTEGRATING)
    integration_path = manager.validate(integration_worktree)
    git(integration_path, "cherry-pick", task_commit.commit_sha)
    resulting_commit = git(integration_path, "rev-parse", "HEAD")
    coordinator = IntegrationCoordinator(store, manager)

    recovered = coordinator.recover_interrupted("run-1", integration_worktree)

    assert recovered[0].integration_id == attempt.integration_id
    assert recovered[0].status == MergeStatus.INTEGRATED
    assert recovered[0].resulting_commit == resulting_commit
    assert store.get_task("run-1", "task-a").status == TaskStatus.SUCCEEDED
    assert coordinator.integrate(integration_worktree, task_commit).integration_id == attempt.integration_id
    assert len(store.list_integrations("run-1")) == 1
    assert git(source, "rev-parse", "HEAD") == base
    assert git(source, "status", "--porcelain") == ""
