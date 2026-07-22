from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.integration import IntegrationCoordinator
from agentbus.execution.leases import LeaseService
from agentbus.execution.models import RunStatus, TaskExecutionResult, TaskStatus
from agentbus.execution.scheduler import ParallelExecutionScheduler
from agentbus.execution.state_store import StateStore
from agentbus.execution.worker import LocalTaskWorker
from agentbus.git.repository import GitRepository
from agentbus.runtime.orchestrator import MultiAgentOrchestrator
from agentbus.worktrees.manager import GitWorktreeManager


PLAN = {
    "goal": "Build independent modules then integration tests",
    "steps": [
        {
            "id": "task-A",
            "title": "Module A",
            "description": "Write module_a.py",
            "dependencies": [],
            "risk": "low",
        },
        {
            "id": "task-B",
            "title": "Module B",
            "description": "Write module_b.py",
            "dependencies": [],
            "risk": "low",
        },
        {
            "id": "task-C",
            "title": "Integration tests",
            "description": "Write integration_test.py",
            "dependencies": ["task-A", "task-B"],
            "risk": "low",
        },
    ],
    "test_strategy": "offline fake",
    "done_criteria": ["all modules integrated"],
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


def create_parallel_run(store, workspace, base):
    return DurableExecutionEngine(store).create_run(
        "Parallel modules",
        PLAN,
        model="fake",
        workspace=str(workspace),
        run_id="parallel-run",
        metadata={
            "final_review": {"required": True, "status": "pending"},
            "git": {
                "requested": True,
                "create_branch": True,
                "commit_changes": True,
                "open_pr": False,
                "pr_base": "main",
                "branch_name": "feature/parallel-result",
            },
            "parallel_execution": {
                "enabled": True,
                "max_workers": 2,
                "lease_seconds": 60,
                "heartbeat_seconds": 5,
                "base_commit": base,
                "workers_used": [],
                "integration_order": [],
            },
        },
    )


def test_independent_tasks_overlap_and_dependency_waits_for_integration(tmp_path):
    source, base = setup_repository(tmp_path / "repo")
    store = StateStore(tmp_path / "state.db")
    create_parallel_run(store, source, base)
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    leases = LeaseService(store, lease_seconds=60)
    integration = IntegrationCoordinator(store, manager)
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    calls = []
    active = 0
    maximum_active = 0

    class Executor:
        def __init__(self, workspace):
            self.workspace = workspace

        def execute(self, context):
            nonlocal active, maximum_active
            task_id = context.task.task_id
            with lock:
                calls.append(task_id)
                active += 1
                maximum_active = max(maximum_active, active)
            if task_id in {"task-A", "task-B"}:
                barrier.wait(timeout=10)
            if task_id == "task-A":
                path = "module_a.py"
                content = "VALUE_A = 1\n"
            elif task_id == "task-B":
                path = "module_b.py"
                content = "VALUE_B = 2\n"
            else:
                assert (self.workspace / "module_a.py").is_file()
                assert (self.workspace / "module_b.py").is_file()
                path = "integration_test.py"
                content = "from module_a import VALUE_A\nfrom module_b import VALUE_B\n"
            (self.workspace / path).write_text(content, encoding="utf-8")
            with lock:
                active -= 1
            return TaskExecutionResult(
                succeeded=True,
                summary=f"completed {task_id}",
                changed_files=[path],
                verifier_status="passed",
            )

    def worker_factory(worker_id):
        return LocalTaskWorker(
            worker_id=worker_id,
            store=store,
            lease_service=leases,
            worktree_manager=manager,
            executor_factory=lambda path: Executor(path),
            heartbeat_seconds=5,
        )

    scheduler = ParallelExecutionScheduler(
        store=store,
        worktree_manager=manager,
        lease_service=leases,
        integration=integration,
        worker_factory=worker_factory,
        max_workers=2,
    )

    report = scheduler.run("parallel-run")

    assert report.status == RunStatus.WAITING_FOR_REVIEW
    assert maximum_active == 2
    assert calls.count("task-A") == calls.count("task-B") == calls.count("task-C") == 1
    assert calls.index("task-C") > calls.index("task-A")
    assert calls.index("task-C") > calls.index("task-B")
    assert [item.task_id for item in store.list_integrations("parallel-run")] == [
        "task-A",
        "task-B",
        "task-C",
    ]
    assert all(
        task.status == TaskStatus.SUCCEEDED
        for task in store.list_tasks("parallel-run")
    )
    assert len(store.list_task_commits("parallel-run")) == 3
    assert all(len(store.list_attempts("parallel-run", task_id)) == 1 for task_id in calls)
    assert all(lease.status.value == "released" for lease in leases.list_leases("parallel-run"))
    integration_worktree = next(
        item for item in store.list_worktrees("parallel-run") if item.task_id is None
    )
    integrated = integration_worktree.path
    assert (tmp_path / "worktrees").is_dir()
    assert GitRepository(integrated).head_commit(short=False) == report.integration_commit
    assert all((Path(integrated) / name).is_file() for name in (
        "module_a.py",
        "module_b.py",
        "integration_test.py",
    ))
    task_worktrees = {
        item.task_id: item for item in store.list_worktrees("parallel-run") if item.task_id
    }
    assert task_worktrees["task-A"].base_commit == base
    assert task_worktrees["task-B"].base_commit == base
    assert task_worktrees["task-C"].base_commit != base
    assert git(source, "status", "--porcelain") == ""
    assert git(source, "rev-parse", "HEAD") == base

    class FinalVerifier:
        def __init__(self):
            self.calls = 0

        def verify(
            self,
            require_command=False,
            *,
            tool_runtime=None,
            run_id=None,
            task_id=None,
            invocation_key=None,
            **kwargs,
        ):
            self.calls += 1
            assert require_command is True
            assert tool_runtime.workspace == source
            assert tool_runtime.worktree == Path(integrated)
            assert run_id == "parallel-run"
            assert task_id == "task-C"
            assert invocation_key == "final-integration"
            assert all(
                (Path(integrated) / name).is_file()
                for name in ("module_a.py", "module_b.py", "integration_test.py")
            )
            return {
                "passed": True,
                "command": ["fake", "verify"],
                "exit_code": 0,
                "output": "integrated verification passed",
                "reason": "offline fake",
            }

    class FinalReviewer:
        def __init__(self):
            self.calls = 0
            self.diff = ""

        def review(self, user_task, plan, git_diff, test_output=None):
            self.calls += 1
            self.diff = git_diff
            return {
                "approved": True,
                "issues": [],
                "summary": "Integrated result approved",
                "required_fixes": [],
            }

    final_verifier = FinalVerifier()
    final_reviewer = FinalReviewer()
    GitRepository(str(source)).create_branch_at(
        "feature/parallel-result", report.integration_commit
    )
    settings = AgentBusConfig(
        workspace_dir=str(source),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "unused-state"),
        parallel_execution=True,
        max_workers=2,
        worktree_root=str(tmp_path / "worktrees"),
    )
    runner = MultiAgentOrchestrator(
        config=settings,
        state_store=store,
        git_repository=GitRepository(str(source)),
        parallel_executor_factory=lambda path: (_ for _ in ()).throw(
            AssertionError("successful tasks must not rerun")
        ),
        parallel_final_verifier=final_verifier,
        parallel_final_reviewer=final_reviewer,
        create_branch=True,
        branch_name="feature/parallel-result",
        commit_changes=True,
    )

    completed = runner.run_durable("parallel-run", resume=True)
    assert completed.status == RunStatus.SUCCEEDED
    assert completed.verifier_status == "passed"
    assert completed.reviewer_status == "approved"
    assert completed.commit_identifier == completed.integration_commit
    assert completed.original_base_commit == base
    assert completed.final_branch == "feature/parallel-result"
    assert final_verifier.calls == 1
    assert final_reviewer.calls == 1
    assert "module_a.py" in final_reviewer.diff
    assert "module_b.py" in final_reviewer.diff
    assert (
        git(source, "rev-parse", "feature/parallel-result")
        == completed.integration_commit
    )
    assert git(source, "rev-parse", "HEAD") == base
    assert git(source, "status", "--porcelain") == ""

    resumed = runner.resume_durable("parallel-run")
    assert resumed.status == RunStatus.SUCCEEDED
    assert calls.count("task-A") == calls.count("task-B") == calls.count("task-C") == 1
    assert final_verifier.calls == 1
    assert final_reviewer.calls == 1


def test_high_risk_task_waits_without_creating_worker_or_worktree(tmp_path):
    source, base = setup_repository(tmp_path / "repo")
    plan = {
        **PLAN,
        "steps": [{**PLAN["steps"][0], "risk": "high"}],
    }
    store = StateStore(tmp_path / "state.db")
    DurableExecutionEngine(store).create_run(
        "High risk",
        plan,
        model="fake",
        workspace=str(source),
        run_id="parallel-run",
        metadata={
            "final_review": {"required": True, "status": "pending"},
            "parallel_execution": {"enabled": True, "max_workers": 2, "base_commit": base},
        },
    )
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    leases = LeaseService(store)
    scheduler = ParallelExecutionScheduler(
        store=store,
        worktree_manager=manager,
        lease_service=leases,
        integration=IntegrationCoordinator(store, manager),
        worker_factory=lambda worker_id: (_ for _ in ()).throw(
            AssertionError("worker must not be created")
        ),
        max_workers=2,
    )

    report = scheduler.run("parallel-run")

    assert report.status == RunStatus.WAITING_FOR_APPROVAL
    assert report.pending_approvals == ["task-A"]
    assert leases.list_leases("parallel-run") == []
    assert not [item for item in store.list_worktrees("parallel-run") if item.task_id]


def test_resume_does_not_reclaim_task_with_valid_active_lease(tmp_path):
    source, base = setup_repository(tmp_path / "repo")
    store = StateStore(tmp_path / "state.db")
    DurableExecutionEngine(store).create_run(
        "Lease-aware resume",
        {**PLAN, "steps": [PLAN["steps"][0]]},
        model="fake",
        workspace=str(source),
        run_id="parallel-run",
        metadata={
            "final_review": {"required": True, "status": "pending"},
            "parallel_execution": {
                "enabled": True,
                "max_workers": 2,
                "base_commit": base,
            },
        },
    )
    store.update_run_status(
        "parallel-run", RunStatus.RUNNING, event_type="test_run_started"
    )
    store.update_task_status(
        "parallel-run", "task-A", TaskStatus.READY, event_type="test_task_ready"
    )
    leases = LeaseService(store, lease_seconds=60)
    lease = leases.acquire_lease(
        "parallel-run", "task-A", "healthy-worker", activate_task=True
    )
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    scheduler = ParallelExecutionScheduler(
        store=store,
        worktree_manager=manager,
        lease_service=leases,
        integration=IntegrationCoordinator(store, manager),
        worker_factory=lambda worker_id: (_ for _ in ()).throw(
            AssertionError("a healthy leased task must not be reclaimed")
        ),
        max_workers=2,
    )

    report = scheduler.run("parallel-run", resume=True)

    assert report.status == RunStatus.RUNNING
    assert store.get_task("parallel-run", "task-A").status == TaskStatus.RUNNING
    assert leases.get_active_lease("parallel-run", "task-A").lease_id == lease.lease_id
    assert store.list_attempts("parallel-run", "task-A") == []


@pytest.mark.parametrize("failure_stage", ["verification", "review"])
def test_final_acceptance_failure_prevents_branch_commit_and_pr(
    tmp_path, failure_stage
):
    source, base = setup_repository(tmp_path / "repo")
    store = StateStore(tmp_path / "state.db")
    DurableExecutionEngine(store).create_run(
        "Final acceptance gate",
        {**PLAN, "steps": [PLAN["steps"][0]]},
        model="fake",
        workspace=str(source),
        run_id="gated-run",
        metadata={
            "final_review": {"required": True, "status": "pending"},
            "git": {
                "requested": True,
                "create_branch": True,
                "commit_changes": True,
                "open_pr": True,
                "pr_base": "main",
                "branch_name": "feature/must-not-exist",
            },
            "parallel_execution": {
                "enabled": True,
                "max_workers": 1,
                "lease_seconds": 60,
                "heartbeat_seconds": 5,
                "base_commit": base,
                "worktree_root": str(tmp_path / "worktrees"),
            },
        },
    )
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    leases = LeaseService(store, lease_seconds=60)

    class Executor:
        def __init__(self, workspace):
            self.workspace = workspace

        def execute(self, context):
            (self.workspace / "module_a.py").write_text(
                "VALUE_A = 1\n", encoding="utf-8"
            )
            return TaskExecutionResult(
                succeeded=True,
                summary="task complete",
                changed_files=["module_a.py"],
                verifier_status="passed",
            )

    scheduler = ParallelExecutionScheduler(
        store=store,
        worktree_manager=manager,
        lease_service=leases,
        integration=IntegrationCoordinator(store, manager),
        worker_factory=lambda worker_id: LocalTaskWorker(
            worker_id=worker_id,
            store=store,
            lease_service=leases,
            worktree_manager=manager,
            executor_factory=lambda path: Executor(path),
            heartbeat_seconds=5,
        ),
        max_workers=1,
    )
    assert scheduler.run("gated-run").status == RunStatus.WAITING_FOR_REVIEW

    class FinalVerifier:
        def verify(self, require_command=False):
            return {
                "passed": failure_stage != "verification",
                "command": ["fake", "verify"],
                "exit_code": 1 if failure_stage == "verification" else 0,
                "output": "offline final verification",
                "reason": "verification failed" if failure_stage == "verification" else "passed",
            }

    class FinalReviewer:
        def __init__(self):
            self.calls = 0

        def review(self, user_task, plan, git_diff, test_output=None):
            self.calls += 1
            return {
                "approved": False,
                "issues": [{"severity": "high", "message": "Reject integrated work"}],
                "summary": "Integrated result rejected",
                "required_fixes": ["Correct the integrated implementation"],
            }

    class PullRequests:
        def __init__(self):
            self.calls = []

        def create_pr(self, **kwargs):
            self.calls.append(kwargs)
            return "https://example.invalid/pr/1"

    reviewer = FinalReviewer()
    pull_requests = PullRequests()
    settings = AgentBusConfig(
        workspace_dir=str(source),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "unused-state"),
        parallel_execution=True,
        worktree_root=str(tmp_path / "worktrees"),
    )
    runner = MultiAgentOrchestrator(
        config=settings,
        state_store=store,
        git_repository=GitRepository(str(source)),
        pr_client=pull_requests,
        parallel_executor_factory=lambda path: (_ for _ in ()).throw(
            AssertionError("integrated task must not rerun")
        ),
        parallel_final_verifier=FinalVerifier(),
        parallel_final_reviewer=reviewer,
    )

    report = runner.resume_durable("gated-run")

    assert report.status == RunStatus.FAILED
    assert report.commit_identifier is None
    assert report.pr_url is None
    assert pull_requests.calls == []
    assert git(source, "branch", "--list", "feature/must-not-exist") == ""
    assert git(source, "rev-parse", "HEAD") == base
    assert git(source, "status", "--porcelain") == ""
    assert store.get_task("gated-run", "task-A").status == TaskStatus.SUCCEEDED
    assert len(store.list_task_commits("gated-run")) == 1
    assert report.changed_files == ["module_a.py"]
    if failure_stage == "verification":
        assert report.verifier_status == "failed"
        assert report.reviewer_status == "not_run"
        assert reviewer.calls == 0
    else:
        assert report.verifier_status == "passed"
        assert report.reviewer_status == "rejected"
        assert reviewer.calls == 1
        assert report.required_fixes == ["Correct the integrated implementation"]


def test_integration_conflict_fails_run_without_finalization_or_source_changes(
    tmp_path,
):
    source, base = setup_repository(tmp_path / "repo")
    (source / "shared.txt").write_text("baseline\n", encoding="utf-8")
    git(source, "add", "shared.txt")
    git(source, "commit", "-q", "-m", "add shared file")
    base = git(source, "rev-parse", "HEAD")
    store = StateStore(tmp_path / "state.db")
    conflict_plan = {
        **PLAN,
        "steps": [PLAN["steps"][0], PLAN["steps"][1]],
    }
    DurableExecutionEngine(store).create_run(
        "Conflicting parallel work",
        conflict_plan,
        model="fake",
        workspace=str(source),
        run_id="conflict-run",
        metadata={
            "final_review": {"required": True, "status": "pending"},
            "git": {
                "requested": True,
                "create_branch": True,
                "commit_changes": True,
                "open_pr": True,
                "pr_base": "main",
                "branch_name": "feature/conflict-must-not-exist",
            },
            "parallel_execution": {
                "enabled": True,
                "max_workers": 2,
                "lease_seconds": 60,
                "heartbeat_seconds": 5,
                "base_commit": base,
                "worktree_root": str(tmp_path / "worktrees"),
            },
        },
    )
    executions = []

    class Executor:
        def __init__(self, workspace):
            self.workspace = workspace

        def execute(self, context):
            task_id = context.task.task_id
            executions.append(task_id)
            (self.workspace / "shared.txt").write_text(
                f"content from {task_id}\n", encoding="utf-8"
            )
            return TaskExecutionResult(
                succeeded=True,
                summary=f"completed {task_id}",
                changed_files=["shared.txt"],
                verifier_status="passed",
            )

    class MustNotFinalize:
        def verify(self, require_command=False):
            pytest.fail("final verification must not run after integration conflict")

        def review(self, **kwargs):
            pytest.fail("final review must not run after integration conflict")

    class PullRequests:
        def __init__(self):
            self.calls = []

        def create_pr(self, **kwargs):
            self.calls.append(kwargs)
            return "https://example.invalid/pr/1"

    pull_requests = PullRequests()
    settings = AgentBusConfig(
        workspace_dir=str(source),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "unused-state"),
        parallel_execution=True,
        max_workers=2,
        worktree_root=str(tmp_path / "worktrees"),
    )
    runner = MultiAgentOrchestrator(
        config=settings,
        state_store=store,
        git_repository=GitRepository(str(source)),
        pr_client=pull_requests,
        parallel_executor_factory=lambda path: Executor(path),
        parallel_final_verifier=MustNotFinalize(),
        parallel_final_reviewer=MustNotFinalize(),
    )

    report = runner.run_durable("conflict-run")

    assert report.status == RunStatus.FAILED
    assert sorted(executions) == ["task-A", "task-B"]
    assert len(store.list_task_commits("conflict-run")) == 2
    assert report.integration_conflicts[0]["conflict_files"] == ["shared.txt"]
    assert report.commit_identifier is None
    assert report.pr_url is None
    assert pull_requests.calls == []
    assert git(source, "branch", "--list", "feature/conflict-must-not-exist") == ""
    assert (source / "shared.txt").read_text(encoding="utf-8") == "baseline\n"
    assert git(source, "rev-parse", "HEAD") == base
    assert git(source, "status", "--porcelain") == ""


def test_failed_parallel_run_reports_retained_file_side_effects(tmp_path):
    source, base = setup_repository(tmp_path / "repo")
    store = StateStore(tmp_path / "state.db")
    DurableExecutionEngine(store).create_run(
        "Fail after editing",
        {**PLAN, "steps": [PLAN["steps"][0]]},
        model="fake",
        workspace=str(source),
        run_id="failed-run",
        metadata={
            "parallel_execution": {
                "enabled": True,
                "max_workers": 1,
                "base_commit": base,
                "worktree_root": str(tmp_path / "worktrees"),
            }
        },
    )
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    leases = LeaseService(store)

    class FailingExecutor:
        def __init__(self, workspace):
            self.workspace = workspace

        def execute(self, context):
            (self.workspace / "broken.py").write_text(
                "BROKEN = True\n", encoding="utf-8"
            )
            return TaskExecutionResult(
                succeeded=False,
                summary="verification failed",
                failure_category="verifier_failure",
                error_message="offline verifier rejected the edit",
                retryable=False,
                changed_files=["broken.py"],
                verifier_status="failed",
            )

    scheduler = ParallelExecutionScheduler(
        store=store,
        worktree_manager=manager,
        lease_service=leases,
        integration=IntegrationCoordinator(store, manager),
        worker_factory=lambda worker_id: LocalTaskWorker(
            worker_id=worker_id,
            store=store,
            lease_service=leases,
            worktree_manager=manager,
            executor_factory=lambda path: FailingExecutor(path),
            heartbeat_seconds=5,
        ),
        max_workers=1,
    )

    report = scheduler.run("failed-run")

    assert report.status == RunStatus.FAILED
    assert report.changed_files == ["broken.py"]
    assert report.side_effects_persisted is True
    assert len(report.retained_worktrees) == 2
    task_worktree = Path(report.task_worktrees["task-A"])
    assert (task_worktree / "broken.py").read_text(encoding="utf-8") == "BROKEN = True\n"
    assert git(source, "status", "--porcelain") == ""


def test_interrupted_worker_stops_after_persisted_attempt_limit(tmp_path):
    source, base = setup_repository(tmp_path / "repo")
    store = StateStore(tmp_path / "state.db")
    DurableExecutionEngine(store).create_run(
        "Bound unexpected worker interruption",
        {**PLAN, "steps": [PLAN["steps"][0]]},
        model="fake",
        workspace=str(source),
        run_id="interrupted-run",
        metadata={
            "parallel_execution": {
                "enabled": True,
                "max_workers": 1,
                "base_commit": base,
                "worktree_root": str(tmp_path / "worktrees"),
            }
        },
    )
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    leases = LeaseService(store)
    executions = 0

    class InterruptingExecutor:
        def execute(self, context):
            nonlocal executions
            executions += 1
            raise RuntimeError("simulated unexpected worker interruption")

    scheduler = ParallelExecutionScheduler(
        store=store,
        worktree_manager=manager,
        lease_service=leases,
        integration=IntegrationCoordinator(store, manager),
        worker_factory=lambda worker_id: LocalTaskWorker(
            worker_id=worker_id,
            store=store,
            lease_service=leases,
            worktree_manager=manager,
            executor_factory=lambda path: InterruptingExecutor(),
            heartbeat_seconds=5,
        ),
        max_workers=1,
    )

    report = scheduler.run("interrupted-run")

    task = store.get_task("interrupted-run", "task-A")
    attempts = store.list_attempts("interrupted-run", "task-A")
    assert report.status == RunStatus.FAILED
    assert task.status == TaskStatus.FAILED
    assert task.current_attempt_count == task.spec.maximum_attempts == 2
    assert executions == 2
    assert [attempt.status.value for attempt in attempts] == [
        "interrupted",
        "interrupted",
    ]
    assert all(
        lease.status.value != "active"
        for lease in leases.list_leases("interrupted-run")
    )
