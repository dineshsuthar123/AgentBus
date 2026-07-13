from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.integration import IntegrationCoordinator
from agentbus.execution.leases import LeaseService, LeaseStatus
from agentbus.execution.models import RunStatus, TaskExecutionResult, TaskStatus
from agentbus.execution.scheduler import ParallelExecutionScheduler
from agentbus.execution.state_store import StateStore
from agentbus.execution.worker import LocalTaskWorker
from agentbus.git.repository import GitRepository
from agentbus.runtime.orchestrator import MultiAgentOrchestrator
from agentbus.worktrees.manager import GitWorktreeManager


PLAN = {
    "goal": "Recover independent workers",
    "steps": [
        {
            "id": "task-A",
            "title": "Module A",
            "description": "Write module_a.py",
            "risk": "low",
            "maximum_attempts": 3,
        },
        {
            "id": "task-B",
            "title": "Module B",
            "description": "Write module_b.py",
            "risk": "low",
            "maximum_attempts": 3,
        },
    ],
    "test_strategy": "offline fake",
    "done_criteria": ["both modules integrated"],
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


def setup_repository(path):
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "AgentBus Test")
    git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "README.md").write_text("baseline\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-q", "-m", "baseline")
    return path.resolve(), git(path, "rev-parse", "HEAD")


def test_process_recreation_recovers_commit_and_expired_lease_without_duplicate_work(
    tmp_path,
):
    source, base = setup_repository(tmp_path / "repo")
    database = tmp_path / "state.db"
    worktree_root = tmp_path / "worktrees"
    first_store = StateStore(database)
    DurableExecutionEngine(first_store).create_run(
        "Recover independent workers",
        PLAN,
        model="fake",
        workspace=str(source),
        run_id="recovery-run",
        metadata={
            "final_review": {"required": True, "status": "pending"},
            "parallel_execution": {
                "enabled": True,
                "max_workers": 2,
                "lease_seconds": 60,
                "heartbeat_seconds": 5,
                "base_commit": base,
                "worktree_root": str(worktree_root),
                "workers_used": [],
                "integration_order": [],
            },
        },
    )
    first_store.update_run_status(
        "recovery-run", RunStatus.RUNNING, event_type="test_run_started"
    )
    first_store.update_task_status(
        "recovery-run", "task-A", TaskStatus.READY, event_type="test_task_ready"
    )
    first_leases = LeaseService(first_store, lease_seconds=60)
    lease_a = first_leases.acquire_lease(
        "recovery-run", "task-A", "interrupted-worker", activate_task=True
    )
    first_manager = GitWorktreeManager(source, worktree_root, first_store)
    executions = {"task-A": 0, "task-B": 0}

    class FirstExecutor:
        def __init__(self, workspace):
            self.workspace = workspace

        def execute(self, context):
            executions[context.task.task_id] += 1
            (self.workspace / "module_a.py").write_text(
                "VALUE_A = 1\n", encoding="utf-8"
            )
            return TaskExecutionResult(
                succeeded=True,
                summary="module A committed before process loss",
                changed_files=["module_a.py"],
                verifier_status="passed",
            )

    def crash_after_commit(stage, run_id, task_id):
        if stage == "after_task_commit":
            raise SystemExit("simulated process loss")

    interrupted = LocalTaskWorker(
        worker_id="interrupted-worker",
        store=first_store,
        lease_service=first_leases,
        worktree_manager=first_manager,
        executor_factory=lambda path: FirstExecutor(path),
        heartbeat_seconds=5,
        crash_hook=crash_after_commit,
    )
    with pytest.raises(SystemExit, match="simulated process loss"):
        interrupted.execute(
            first_store.get_run("recovery-run"),
            first_store.get_task("recovery-run", "task-A"),
            lease_a,
            base,
        )
    assert first_store.get_task_commit("recovery-run", "task-A") is None
    assert first_store.get_task("recovery-run", "task-A").status == TaskStatus.RUNNING

    first_store.update_task_status(
        "recovery-run", "task-B", TaskStatus.READY, event_type="test_task_ready"
    )
    old_clock = lambda: datetime(2000, 1, 1, tzinfo=timezone.utc)
    stale_lease = LeaseService(
        first_store, lease_seconds=1, clock=old_clock
    ).acquire_lease(
        "recovery-run", "task-B", "expired-worker", activate_task=True
    )

    store = StateStore(database)
    manager = GitWorktreeManager(source, worktree_root, store)
    leases = LeaseService(store, lease_seconds=60)

    class ResumedExecutor:
        def __init__(self, workspace):
            self.workspace = workspace

        def execute(self, context):
            task_id = context.task.task_id
            executions[task_id] += 1
            if task_id == "task-A":
                pytest.fail("task A code must not rerun after its commit is recovered")
            (self.workspace / "module_b.py").write_text(
                "VALUE_B = 2\n", encoding="utf-8"
            )
            return TaskExecutionResult(
                succeeded=True,
                summary="module B completed after lease recovery",
                changed_files=["module_b.py"],
                verifier_status="passed",
            )

    def worker_factory(worker_id):
        return LocalTaskWorker(
            worker_id=worker_id,
            store=store,
            lease_service=leases,
            worktree_manager=manager,
            executor_factory=lambda path: ResumedExecutor(path),
            heartbeat_seconds=5,
        )

    scheduler = ParallelExecutionScheduler(
        store=store,
        worktree_manager=manager,
        lease_service=leases,
        integration=IntegrationCoordinator(store, manager),
        worker_factory=worker_factory,
        max_workers=2,
    )
    execution_report = scheduler.run("recovery-run", resume=True)

    assert execution_report.status == RunStatus.WAITING_FOR_REVIEW
    assert executions == {"task-A": 1, "task-B": 1}
    assert leases.get_lease(stale_lease.lease_id).status == LeaseStatus.EXPIRED
    assert [lease.fencing_token for lease in leases.list_leases("recovery-run")] == [
        1,
        1,
        2,
        2,
    ]
    assert len(store.list_attempts("recovery-run", "task-A")) == 2
    assert len(store.list_attempts("recovery-run", "task-B")) == 1
    assert len(store.list_task_commits("recovery-run")) == 2
    assert all(
        task.status == TaskStatus.SUCCEEDED
        for task in store.list_tasks("recovery-run")
    )

    integration_path = next(
        item.path for item in store.list_worktrees("recovery-run") if item.task_id is None
    )

    class FinalVerifier:
        def verify(self, require_command=False):
            assert require_command is True
            assert (Path(integration_path) / "module_a.py").is_file()
            assert (Path(integration_path) / "module_b.py").is_file()
            return {
                "passed": True,
                "command": ["fake", "verify"],
                "exit_code": 0,
                "output": "recovered integration passed",
                "reason": "offline fake",
            }

    class FinalReviewer:
        def review(self, user_task, plan, git_diff, test_output=None):
            assert "module_a.py" in git_diff
            assert "module_b.py" in git_diff
            return {
                "approved": True,
                "issues": [],
                "summary": "Recovered integration approved",
                "required_fixes": [],
            }

    settings = AgentBusConfig(
        workspace_dir=str(source),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "unused-state"),
        parallel_execution=True,
        max_workers=2,
        worktree_root=str(worktree_root),
    )
    runner = MultiAgentOrchestrator(
        config=settings,
        state_store=store,
        git_repository=GitRepository(str(source)),
        parallel_executor_factory=lambda path: (_ for _ in ()).throw(
            AssertionError("integrated tasks must not rerun")
        ),
        parallel_final_verifier=FinalVerifier(),
        parallel_final_reviewer=FinalReviewer(),
    )
    completed = runner.resume_durable("recovery-run")

    assert completed.status == RunStatus.SUCCEEDED
    assert executions == {"task-A": 1, "task-B": 1}
    assert git(source, "rev-parse", "HEAD") == base
    assert git(source, "status", "--porcelain") == ""
    event_types = [item["event_type"] for item in store.list_events("recovery-run")]
    assert "task_commit_recovered" in event_types
    assert "lease_expired" in event_types
    assert "final_integration_review_completed" in event_types
