import subprocess
from pathlib import Path

from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.integration import IntegrationCoordinator
from agentbus.execution.leases import LeaseService
from agentbus.execution.models import RunStatus
from agentbus.execution.scheduler import ParallelExecutionScheduler
from agentbus.execution.state_store import StateStore
from agentbus.execution.worker import LocalTaskWorker
from agentbus.git.repository import GitRepository
from agentbus.runtime.durable_workflow import MultiAgentTaskExecutor
from agentbus.tools.git_tools import GitTools
from agentbus.worktrees.manager import GitWorktreeManager


def _git(workspace: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        capture_output=True,
        check=True,
        shell=False,
        text=True,
    )
    return completed.stdout.strip()


def _repository(path: Path) -> tuple[Path, str]:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "AgentBus Tests")
    _git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "foo.txt").write_text("old\n", encoding="utf-8")
    _git(path, "add", "foo.txt")
    _git(path, "commit", "-q", "-m", "baseline")
    return path, _git(path, "rev-parse", "HEAD")


def test_parallel_retry_reuses_task_baseline_across_worktree_recovery(
    tmp_path: Path,
) -> None:
    source, base = _repository(tmp_path / "repo")
    store = StateStore(tmp_path / "state.db")
    plan = {
        "goal": "Retain a rejected parallel task edit",
        "steps": [
            {
                "id": "step-1",
                "title": "Update foo",
                "description": "Update foo once and retain it for retry",
                "risk": "low",
                "execution_kind": "implementation",
                "required_capabilities": ["filesystem.write"],
                "expected_outputs": ["foo.txt"],
                "done_criteria": ["foo contains new"],
                "maximum_attempts": 2,
            }
        ],
        "test_strategy": "Offline fake verifier",
        "done_criteria": ["Task succeeds"],
    }
    DurableExecutionEngine(store).create_run(
        "Update foo in parallel",
        plan,
        model="fake",
        workspace=str(source),
        run_id="parallel-baseline-run",
        metadata={
            "final_review": {"required": True, "status": "pending"},
            "parallel_execution": {
                "enabled": True,
                "max_workers": 1,
                "base_commit": base,
            },
        },
    )
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    leases = LeaseService(store, lease_seconds=60)
    reviewer_calls: list[dict] = []

    class Coder:
        def __init__(self, workspace: Path) -> None:
            self.workspace = workspace

        def execute(self, **kwargs) -> str:
            if int(kwargs["attempt_number"]) == 1:
                (self.workspace / "foo.txt").write_text("new\n", encoding="utf-8")
            return "parallel coder complete"

    class Verifier:
        def verify(self, **kwargs) -> dict:
            return {
                "passed": True,
                "status": "passed",
                "command": ["fake", "verify"],
                "exit_code": 0,
                "output": "passed",
                "reason": "offline fake",
            }

    class Reviewer:
        def review_task(self, **kwargs) -> dict:
            reviewer_calls.append(kwargs)
            approved = int(kwargs["review_evidence"]["attempt_number"]) == 2
            return {
                "approved": approved,
                "issues": [],
                "summary": "approved" if approved else "retry once",
                "required_fixes": [] if approved else ["retry"],
            }

    def executor(workspace: Path):
        return MultiAgentTaskExecutor(
            coder=Coder(workspace),
            verifier=Verifier(),
            reviewer=Reviewer(),
            git_tools=GitTools(str(workspace)),
            git_repository=GitRepository(str(workspace)),
            workspace=str(workspace),
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
            executor_factory=executor,
            heartbeat_seconds=5,
        ),
        max_workers=1,
    )

    report = scheduler.run("parallel-baseline-run")
    attempts = store.list_attempts("parallel-baseline-run", "step-1")

    assert report.status == RunStatus.WAITING_FOR_REVIEW
    assert len(attempts) == 2
    assert len(reviewer_calls) == 2
    assert "-old" in reviewer_calls[1]["task_diff"]
    assert "+new" in reviewer_calls[1]["task_diff"]
    assert attempts[0].metadata["repository_baselines"]["task"] == attempts[1].metadata[
        "repository_baselines"
    ]["task"]
    assert attempts[1].metadata["repository_baselines"]["retry_workspace"] == (
        "retained_cumulative_workspace"
    )
    assert attempts[1].metadata["attempt_artifact_hygiene"]["changed_files"] == []
    worktrees = store.list_worktrees("parallel-baseline-run", task_id="step-1")
    assert len(worktrees) == 1
    assert store.list_task_commits("parallel-baseline-run")[0].changed_files == [
        "foo.txt"
    ]
