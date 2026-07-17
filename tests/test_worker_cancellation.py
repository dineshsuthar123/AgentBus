from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.integration import IntegrationCoordinator
from agentbus.execution.leases import LeaseService, LeaseStatus
from agentbus.execution.models import (
    AttemptStatus,
    RunStatus,
    TaskExecutionResult,
    TaskStatus,
)
from agentbus.execution.scheduler import ParallelExecutionScheduler
from agentbus.execution.state_store import StateStore
from agentbus.execution.worker import LocalTaskWorker, WorkerStatus
from agentbus.git.repository import GitRepository
from agentbus.worktrees.manager import GitWorktreeManager


PLAN = {
    "goal": "Write one deterministic file",
    "steps": [
        {
            "id": "task-A",
            "title": "Write result",
            "description": "Write result.py",
            "risk": "low",
            "maximum_attempts": 2,
        }
    ],
    "test_strategy": "offline",
    "done_criteria": ["result.py exists"],
}


def git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout.strip()


def repository(path: Path) -> tuple[Path, str]:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "AgentBus Test")
    git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "README.md").write_text("baseline\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-q", "-m", "baseline")
    return path.resolve(), git(path, "rev-parse", "HEAD")


def create_parallel_run(
    store: StateStore,
    source: Path,
    base_commit: str,
    worktrees: Path,
    *,
    run_id: str,
) -> None:
    DurableExecutionEngine(store).create_run(
        "Write one deterministic file",
        PLAN,
        model="deterministic",
        workspace=str(source),
        run_id=run_id,
        metadata={
            "parallel_execution": {
                "enabled": True,
                "max_workers": 1,
                "base_commit": base_commit,
                "worktree_root": str(worktrees),
            }
        },
    )


def test_cancelled_worker_releases_lease_without_creating_worktree(tmp_path):
    source, base = repository(tmp_path / "repo")
    store = StateStore(tmp_path / "state.db")
    worktree_root = tmp_path / "worktrees"
    create_parallel_run(
        store,
        source,
        base,
        worktree_root,
        run_id="preflight-run",
    )
    store.update_run_status("preflight-run", RunStatus.RUNNING)
    store.update_task_status("preflight-run", "task-A", TaskStatus.READY)
    leases = LeaseService(store)
    lease = leases.acquire_lease(
        "preflight-run",
        "task-A",
        "worker-1",
        activate_task=True,
    )
    registry = CancellationRegistry(store)
    token = registry.get("preflight-run")
    token.request("stop before worker starts")
    manager = GitWorktreeManager(source, worktree_root, store)
    worker = LocalTaskWorker(
        worker_id="worker-1",
        store=store,
        lease_service=leases,
        worktree_manager=manager,
        executor_factory=lambda _path: (_ for _ in ()).throw(
            AssertionError("executor must not be created")
        ),
        cancellation=token,
    )

    result = worker.execute(
        store.get_run("preflight-run"),
        store.get_task("preflight-run", "task-A"),
        lease,
        base,
    )

    assert result.status == WorkerStatus.CANCELLED
    assert store.get_task("preflight-run", "task-A").status == TaskStatus.CANCELLED
    assert store.list_attempts("preflight-run", "task-A") == []
    assert store.list_worktrees("preflight-run") == []
    assert leases.get_lease(lease.lease_id).status == LeaseStatus.RELEASED


def test_scheduler_does_not_create_lease_or_worktree_after_prior_request(
    tmp_path,
):
    source, base = repository(tmp_path / "repo")
    store = StateStore(tmp_path / "state.db")
    worktree_root = tmp_path / "worktrees"
    create_parallel_run(
        store,
        source,
        base,
        worktree_root,
        run_id="scheduler-run",
    )
    registry = CancellationRegistry(store)
    token = registry.get("scheduler-run")
    token.request("stop scheduler")
    manager = GitWorktreeManager(source, worktree_root, store)
    leases = LeaseService(store)
    scheduler = ParallelExecutionScheduler(
        store=store,
        worktree_manager=manager,
        lease_service=leases,
        integration=IntegrationCoordinator(store, manager),
        worker_factory=lambda _worker_id: (_ for _ in ()).throw(
            AssertionError("worker must not be created")
        ),
        max_workers=1,
        cancellation=token,
        cancellation_registry=registry,
    )

    report = scheduler.run("scheduler-run")

    assert report.status == RunStatus.CANCELLED
    assert store.list_worker_lease_rows("scheduler-run") == []
    assert store.list_worktrees("scheduler-run") == []
    assert store.get_task("scheduler-run", "task-A").status == TaskStatus.CANCELLED
    events = [event["event_type"] for event in store.list_events("scheduler-run")]
    assert events.index("scheduling_stopped") < events.index("run_cancelled")
    assert events.index("run_cancelled") < events.index(
        "cancellation_cleanup_completed"
    )


def test_commit_that_finishes_after_request_is_persisted_without_retry(
    tmp_path,
    monkeypatch,
):
    source, base = repository(tmp_path / "repo")
    store = StateStore(tmp_path / "state.db")
    worktree_root = tmp_path / "worktrees"
    create_parallel_run(
        store,
        source,
        base,
        worktree_root,
        run_id="commit-run",
    )
    store.update_run_status("commit-run", RunStatus.RUNNING)
    store.update_task_status("commit-run", "task-A", TaskStatus.READY)
    leases = LeaseService(store)
    lease = leases.acquire_lease(
        "commit-run",
        "task-A",
        "worker-1",
        activate_task=True,
    )
    registry = CancellationRegistry(store)
    token = registry.get("commit-run")
    manager = GitWorktreeManager(source, worktree_root, store)
    commit_started = threading.Event()
    release_commit = threading.Event()
    original_commit = GitRepository.commit

    def blocking_commit(repository, *args, **kwargs):
        commit_started.set()
        assert release_commit.wait(timeout=5)
        return original_commit(repository, *args, **kwargs)

    monkeypatch.setattr(GitRepository, "commit", blocking_commit)

    class Executor:
        def __init__(self, workspace: Path):
            self.workspace = workspace

        def execute(self, _context):
            (self.workspace / "result.py").write_text(
                "RESULT = 42\n",
                encoding="utf-8",
            )
            return TaskExecutionResult(
                succeeded=True,
                summary="result complete",
                changed_files=["result.py"],
                verifier_status="passed",
            )

    worker = LocalTaskWorker(
        worker_id="worker-1",
        store=store,
        lease_service=leases,
        worktree_manager=manager,
        executor_factory=Executor,
        cancellation=token,
    )
    results = []
    thread = threading.Thread(
        target=lambda: results.append(
            worker.execute(
                store.get_run("commit-run"),
                store.get_task("commit-run", "task-A"),
                lease,
                base,
            )
        )
    )
    thread.start()
    assert commit_started.wait(timeout=5)

    token.request("cancel while commit is in progress")
    release_commit.set()
    thread.join(timeout=5)

    assert thread.is_alive() is False
    assert results[0].status == WorkerStatus.SUCCEEDED
    assert store.get_task("commit-run", "task-A").status == (
        TaskStatus.INTEGRATION_PENDING
    )
    assert store.list_attempts("commit-run", "task-A")[-1].status == (
        AttemptStatus.SUCCEEDED
    )
    commit = store.get_task_commit("commit-run", "task-A")
    assert commit is not None
    assert commit.changed_files == ["result.py"]
    assert leases.get_lease(lease.lease_id).status == LeaseStatus.RELEASED
    state = store.get_cancellation_state("commit-run")
    assert state.operations_completed_after_request == ["worker.task_commit"]
    assert state.tasks_completed_after_request == ["task-A"]

    report = DurableExecutionEngine(
        store,
        cancellation=token,
        cancellation_registry=registry,
    ).finalize_cancellation("commit-run")

    assert report.status == RunStatus.CANCELLED
    assert store.get_task_commit("commit-run", "task-A") == commit
    assert store.list_attempts("commit-run", "task-A")[-1].status == (
        AttemptStatus.SUCCEEDED
    )
    assert store.get_task("commit-run", "task-A").status == TaskStatus.CANCELLED
    assert not any(
        event["event_type"] == "task_retry_scheduled"
        for event in store.list_events("commit-run")
    )

