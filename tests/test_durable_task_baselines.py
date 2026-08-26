import subprocess
from pathlib import Path

import pytest

from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.models import AttemptStatus, RunStatus
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepository
from agentbus.runtime.durable_workflow import MultiAgentTaskExecutor
from agentbus.tools.git_tools import GitTools


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


def _repository(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "AgentBus Tests")
    _git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "foo.txt").write_text("old\n", encoding="utf-8")
    (path / "bar.txt").write_text("old bar\n", encoding="utf-8")
    _git(path, "add", "foo.txt", "bar.txt")
    _git(path, "commit", "-q", "-m", "baseline")
    return path


PLAN = {
    "goal": "Exercise durable retry baselines",
    "steps": [
        {
            "id": "step-1",
            "title": "Update task files",
            "description": "Update the requested task files safely",
            "risk": "low",
            "execution_kind": "implementation",
            "required_capabilities": ["filesystem.write"],
            "expected_outputs": ["foo.txt", "bar.txt"],
            "done_criteria": ["The requested candidate state is present"],
            "maximum_attempts": 2,
        }
    ],
    "test_strategy": "Offline fake verifier",
    "done_criteria": ["Task review succeeds"],
}


class _Coder:
    def __init__(self, workspace: Path, scenario: str) -> None:
        self.workspace = workspace
        self.scenario = scenario

    def execute(self, **kwargs) -> str:
        attempt = int(kwargs["attempt_number"])
        if attempt == 1:
            value = "intermediate\n" if self.scenario == "same_file" else "new\n"
            (self.workspace / "foo.txt").write_text(value, encoding="utf-8")
        elif self.scenario == "same_file":
            (self.workspace / "foo.txt").write_text("final\n", encoding="utf-8")
        elif self.scenario == "second_file":
            (self.workspace / "bar.txt").write_text("new bar\n", encoding="utf-8")
        return f"coder attempt {attempt} completed"


class _Verifier:
    def verify(self, **kwargs) -> dict:
        return {
            "passed": True,
            "status": "passed",
            "command": ["fake", "verify"],
            "exit_code": 0,
            "output": "offline verification passed",
            "reason": "offline fake",
        }


class _Reviewer:
    def __init__(self, calls: list[dict]) -> None:
        self.calls = calls

    def review_task(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        approved = int(kwargs["review_evidence"]["attempt_number"]) == 2
        return {
            "approved": approved,
            "issues": [] if approved else [{"severity": "medium", "message": "retry"}],
            "summary": "approved cumulative candidate" if approved else "retry once",
            "required_fixes": [] if approved else ["Retry without discarding edits"],
        }


def _executor(workspace: Path, scenario: str, calls: list[dict]):
    return MultiAgentTaskExecutor(
        coder=_Coder(workspace, scenario),
        verifier=_Verifier(),
        reviewer=_Reviewer(calls),
        git_tools=GitTools(str(workspace)),
        git_repository=GitRepository(str(workspace)),
        workspace=str(workspace),
    )


def _create_run(store: StateStore, workspace: Path, run_id: str) -> None:
    DurableExecutionEngine(store).create_run(
        "Update durable task files",
        PLAN,
        model="fake",
        workspace=str(workspace),
        run_id=run_id,
        metadata={"final_review": {"required": True, "status": "pending"}},
    )


@pytest.mark.parametrize(
    ("scenario", "expected_files", "expected_fragments", "excluded_fragment"),
    [
        ("no_new_edit", ["foo.txt"], ["-old", "+new"], None),
        ("same_file", ["foo.txt"], ["-old", "+final"], "intermediate"),
        (
            "second_file",
            ["bar.txt", "foo.txt"],
            ["+new", "+new bar"],
            None,
        ),
    ],
)
def test_retry_review_uses_persisted_cumulative_task_baseline_after_restart(
    tmp_path: Path,
    scenario: str,
    expected_files: list[str],
    expected_fragments: list[str],
    excluded_fragment: str | None,
) -> None:
    workspace = _repository(tmp_path / "repo")
    store = StateStore(tmp_path / "state.db")
    calls: list[dict] = []
    _create_run(store, workspace, "baseline-run")

    first_engine = DurableExecutionEngine(
        store,
        _executor(workspace, scenario, calls),
    )
    first_report = first_engine.execute_next("baseline-run")
    first_engine.close()

    assert first_report.status == RunStatus.RUNNING
    first_attempt = store.list_attempts("baseline-run", "step-1")[0]
    assert first_attempt.status == AttemptStatus.FAILED

    # A new engine/executor models reconstruction across a process boundary.
    second_engine = DurableExecutionEngine(
        store,
        _executor(workspace, scenario, calls),
    )
    completed = second_engine.execute_next("baseline-run")
    second_engine.close()

    attempts = store.list_attempts("baseline-run", "step-1")
    assert completed.status == RunStatus.WAITING_FOR_REVIEW
    assert len(attempts) == 2
    assert attempts[1].status == AttemptStatus.SUCCEEDED
    assert attempts[0].metadata["repository_baselines"]["task"] == attempts[1].metadata[
        "repository_baselines"
    ]["task"]
    assert (
        attempts[0].metadata["repository_baselines"]["attempt"]["identity_sha256"]
        != attempts[1].metadata["repository_baselines"]["attempt"]["identity_sha256"]
    )
    assert attempts[1].metadata["repository_baselines"]["retry_workspace"] == (
        "retained_cumulative_workspace"
    )
    assert attempts[1].metadata["artifact_hygiene"]["changed_files"] == expected_files
    assert attempts[1].metadata["artifact_hygiene"]["commit_eligible_files"] == (
        expected_files
    )
    assert calls[1]["review_evidence"]["changed_files"] == expected_files
    assert calls[1]["review_evidence"]["commit_eligible_files"] == expected_files
    assert calls[1]["review_evidence"]["diff_scope"] == "cumulative_task"
    assert (
        attempts[1].metadata["verification_evidence"][
            "candidate_identity_sha256"
        ]
        == attempts[1].metadata["review_evidence"]["candidate"][
            "identity_sha256"
        ]
    )
    for fragment in expected_fragments:
        assert fragment in calls[1]["task_diff"]
    if excluded_fragment is not None:
        assert excluded_fragment not in calls[1]["task_diff"]
    if scenario == "no_new_edit":
        assert attempts[1].metadata["attempt_artifact_hygiene"]["changed_files"] == []


def test_retry_restored_before_attempt_has_legitimate_empty_cumulative_diff(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "repo")
    store = StateStore(tmp_path / "state.db")
    calls: list[dict] = []
    _create_run(store, workspace, "restore-run")
    repository = GitRepository(str(workspace))
    original_baseline = repository.capture_review_baseline()
    original = (workspace / "foo.txt").read_bytes()
    first = DurableExecutionEngine(
        store,
        _executor(workspace, "no_new_edit", calls),
    )
    first.execute_next("restore-run")
    first.close()

    # Model a separate, explicit supported restore before retry baseline capture.
    (workspace / "foo.txt").write_bytes(original)
    restored_baseline = repository.capture_review_baseline()
    assert restored_baseline["head_commit"] == original_baseline["head_commit"]
    assert restored_baseline["tree_id"] == original_baseline["tree_id"]
    assert restored_baseline["worktree_snapshot"] == original_baseline[
        "worktree_snapshot"
    ]
    second = DurableExecutionEngine(
        store,
        _executor(workspace, "no_new_edit", calls),
    )
    completed = second.execute_next("restore-run")
    second.close()

    retry = store.list_attempts("restore-run", "step-1")[1]
    baselines = retry.metadata["repository_baselines"]
    assert completed.status == RunStatus.WAITING_FOR_REVIEW
    assert baselines["retry_workspace"] == "restored_to_task_baseline", baselines
    assert retry.metadata["artifact_hygiene"]["changed_files"] == []
    assert calls[1]["task_diff"] == "No diff."
