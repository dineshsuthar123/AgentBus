from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.models import FailureCategory, RunStatus
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepository
from agentbus.models.errors import ModelServiceUnavailableError
from agentbus.runtime.durable_workflow import MultiAgentTaskExecutor
from agentbus.tools.git_tools import GitTools


PLAN = {
    "goal": "Repair one retained candidate",
    "steps": [
        {
            "id": "step-1",
            "title": "Repair value",
            "description": "Update value.txt until verification and review pass.",
            "risk": "low",
            "execution_kind": "implementation",
            "required_capabilities": ["filesystem.write"],
            "expected_outputs": ["value.txt"],
            "done_criteria": ["value.txt contains expected X"],
            "maximum_attempts": 2,
        }
    ],
    "test_strategy": "Use an offline deterministic verifier.",
    "done_criteria": ["The retained candidate passes review."],
}


def test_verifier_failure_feedback_survives_restart_and_repairs_retained_candidate(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "repo")
    state_path = tmp_path / "state.db"
    calls: list[dict] = []
    secret = "retry-feedback-secret-must-not-persist"
    diagnostic_padding = "bounded-diagnostic-" * 2_000
    _create_run(state_path, workspace, "verifier-retry")

    first = DurableExecutionEngine(
        StateStore(state_path),
        _executor(
            workspace,
            calls,
            failure="verifier",
            secret=secret,
            diagnostic_padding=diagnostic_padding,
        ),
    )
    first_report = first.execute_next("verifier-retry")
    first.close()

    assert first_report.status == RunStatus.RUNNING
    assert (workspace / "value.txt").read_text(encoding="utf-8") == "actual Y\n"

    second = DurableExecutionEngine(
        StateStore(state_path),
        _executor(
            workspace,
            calls,
            failure="verifier",
            secret=secret,
            diagnostic_padding=diagnostic_padding,
        ),
    )
    completed = second.execute_next("verifier-retry")
    second.close()

    store = StateStore(state_path)
    attempts = store.list_attempts("verifier-retry", "step-1")
    feedback = calls[1]["retry_feedback"]
    source_evidence = attempts[0].metadata["retry_evidence"]
    persisted_feedback = attempts[1].metadata["retry_feedback"]

    assert completed.status == RunStatus.WAITING_FOR_REVIEW
    assert (workspace / "value.txt").read_text(encoding="utf-8") == "expected X\n"
    assert feedback == persisted_feedback
    assert feedback["source_evidence"] == source_evidence
    assert feedback["source_evidence"]["failure_category"] == "verifier_failure"
    assert feedback["source_evidence"]["source_attempt_id"] == attempts[0].attempt_id
    assert feedback["destination_attempt_id"] == attempts[1].attempt_id
    assert feedback["destination_attempt_number"] == 2
    assert feedback["mutations_retained"] is True
    assert feedback["source_disposition"] == "retained_candidate"
    assert "expected X but got Y" in json.dumps(feedback)
    assert feedback["source_evidence"]["retained_changed_files"] == ["value.txt"]
    assert (
        feedback["active_candidate_identity_sha256"]
        == feedback["source_evidence"]["candidate_identity_sha256"]
    )
    assert secret not in json.dumps(feedback)
    assert secret.encode() not in state_path.read_bytes()
    diagnostics = feedback["source_evidence"]["diagnostics"]
    assert diagnostics["stdout_truncated"] is True
    assert diagnostics["stderr_truncated"] is True
    assert len(diagnostics["stdout"]) <= 8_192
    assert len(diagnostics["stderr"]) <= 8_192
    assert "diagnostic middle truncated" in diagnostics["stdout"]


def test_retryable_provider_failure_is_persisted_before_retry_construction(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "repo")
    state_path = tmp_path / "state.db"
    calls: list[dict] = []
    _create_run(state_path, workspace, "provider-retry")

    first = DurableExecutionEngine(
        StateStore(state_path),
        _executor(workspace, calls, failure="provider"),
    )
    first.execute_next("provider-retry")
    first.close()

    second = DurableExecutionEngine(
        StateStore(state_path),
        _executor(workspace, calls, failure="provider"),
    )
    completed = second.execute_next("provider-retry")
    second.close()

    attempts = StateStore(state_path).list_attempts("provider-retry", "step-1")
    feedback = calls[1]["retry_feedback"]
    assert completed.status == RunStatus.WAITING_FOR_REVIEW
    assert attempts[0].error_category == FailureCategory.MODEL_TRANSPORT_ERROR
    assert feedback["source_evidence"]["failure_category"] == (
        "model_transport_error"
    )
    assert feedback["source_evidence"]["diagnostics"]["kind"] == "model_provider"
    assert "service unavailable" in json.dumps(feedback).lower()


def test_reviewer_rejection_feedback_reaches_retry_and_keeps_cumulative_scope(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "repo")
    state_path = tmp_path / "state.db"
    calls: list[dict] = []
    _create_run(state_path, workspace, "reviewer-retry")

    first = DurableExecutionEngine(
        StateStore(state_path),
        _executor(workspace, calls, failure="reviewer"),
    )
    first.execute_next("reviewer-retry")
    first.close()

    second = DurableExecutionEngine(
        StateStore(state_path),
        _executor(workspace, calls, failure="reviewer"),
    )
    completed = second.execute_next("reviewer-retry")
    second.close()

    store = StateStore(state_path)
    attempts = store.list_attempts("reviewer-retry", "step-1")
    feedback = calls[1]["retry_feedback"]

    assert completed.status == RunStatus.WAITING_FOR_REVIEW
    assert (workspace / "value.txt").read_text(encoding="utf-8") == "reviewed final\n"
    assert feedback["source_evidence"]["failure_category"] == "reviewer_rejection"
    assert "Replace draft with reviewed final" in json.dumps(feedback)
    assert attempts[1].metadata["artifact_hygiene"]["changed_files"] == [
        "value.txt"
    ]
    assert attempts[1].metadata["repository_diff_scope"]["task_review"] == (
        "cumulative_task"
    )


def test_retry_feedback_rejects_unexpected_source_drift_before_coder_runs(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "repo")
    state_path = tmp_path / "state.db"
    calls: list[dict] = []
    _create_run(state_path, workspace, "drifted-retry")

    first = DurableExecutionEngine(
        StateStore(state_path),
        _executor(workspace, calls, failure="reviewer"),
    )
    first.execute_next("drifted-retry")
    first.close()

    (workspace / "value.txt").write_text("unexpected external drift\n", encoding="utf-8")
    second = DurableExecutionEngine(
        StateStore(state_path),
        _executor(workspace, calls, failure="reviewer"),
    )
    failed = second.execute_next("drifted-retry")
    second.close()

    attempts = StateStore(state_path).list_attempts("drifted-retry", "step-1")
    assert failed.status == RunStatus.FAILED
    assert len(calls) == 1
    assert attempts[1].error_category == FailureCategory.RESUMABILITY_FAILURE
    assert "retry evidence" in (attempts[1].error_message or "").lower()


class _Coder:
    def __init__(self, workspace: Path, calls: list[dict], failure: str) -> None:
        self.workspace = workspace
        self.calls = calls
        self.failure = failure

    def execute(self, **kwargs) -> str:
        self.calls.append(kwargs)
        attempt = int(kwargs["attempt_number"])
        feedback = kwargs.get("retry_feedback")
        if self.failure == "provider" and attempt == 1:
            raise ModelServiceUnavailableError(
                "Offline provider service unavailable.",
                provider="deterministic",
                model="fake-coder",
                request_id="safe-request-id",
            )
        if attempt == 1:
            value = "actual Y\n" if self.failure == "verifier" else "draft\n"
            (self.workspace / "value.txt").write_text(value, encoding="utf-8")
        elif self.failure == "verifier" and "expected X but got Y" in json.dumps(
            feedback
        ):
            (self.workspace / "value.txt").write_text("expected X\n", encoding="utf-8")
        elif self.failure == "reviewer" and (
            "Replace draft with reviewed final" in json.dumps(feedback)
        ):
            (self.workspace / "value.txt").write_text(
                "reviewed final\n", encoding="utf-8"
            )
        return f"coder attempt {attempt} complete"


class _Verifier:
    def __init__(
        self,
        workspace: Path,
        failure: str,
        secret: str,
        diagnostic_padding: str,
    ) -> None:
        self.workspace = workspace
        self.failure = failure
        self.secret = secret
        self.diagnostic_padding = diagnostic_padding

    def verify(self, **kwargs) -> dict:
        value = (self.workspace / "value.txt").read_text(encoding="utf-8")
        passed = self.failure != "verifier" or value == "expected X\n"
        stdout = (
            "verification passed"
            if passed
            else (
                f"test_value FAILED: expected X but got Y\n"
                f"api_key={self.secret}\n{self.diagnostic_padding}"
            )
        )
        stderr = (
            ""
            if passed
            else (
                "AssertionError: expected X but got Y\n"
                f"{self.diagnostic_padding}"
            )
        )
        return {
            "passed": passed,
            "status": "passed" if passed else "failed",
            "command": ["offline", "verify"],
            "exit_code": 0 if passed else 1,
            "stdout": stdout,
            "stderr": stderr,
            "output": "\n".join(item for item in (stdout, stderr) if item),
            "reason": "offline deterministic verifier",
        }


class _Reviewer:
    def __init__(self, workspace: Path, failure: str) -> None:
        self.workspace = workspace
        self.failure = failure

    def review_task(self, **kwargs) -> dict:
        value = (self.workspace / "value.txt").read_text(encoding="utf-8")
        approved = self.failure != "reviewer" or value == "reviewed final\n"
        return {
            "approved": approved,
            "issues": [] if approved else [{"message": "Draft remains incomplete"}],
            "summary": "approved" if approved else "The draft needs one correction.",
            "required_fixes": (
                [] if approved else ["Replace draft with reviewed final"]
            ),
        }


def _executor(
    workspace: Path,
    calls: list[dict],
    *,
    failure: str,
    secret: str = "",
    diagnostic_padding: str = "",
) -> MultiAgentTaskExecutor:
    return MultiAgentTaskExecutor(
        coder=_Coder(workspace, calls, failure),
        verifier=_Verifier(workspace, failure, secret, diagnostic_padding),
        reviewer=_Reviewer(workspace, failure),
        git_tools=GitTools(str(workspace)),
        git_repository=GitRepository(str(workspace)),
        workspace=str(workspace),
    )


def _create_run(state_path: Path, workspace: Path, run_id: str) -> None:
    DurableExecutionEngine(StateStore(state_path)).create_run(
        "Repair the retained value candidate.",
        PLAN,
        model="offline-fake",
        workspace=str(workspace),
        run_id=run_id,
        metadata={"final_review": {"required": True, "status": "pending"}},
    )


def _repository(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "AgentBus Tests")
    _git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "value.txt").write_text("original\n", encoding="utf-8")
    _git(path, "add", "value.txt")
    _git(path, "commit", "-q", "-m", "test: initialize retry fixture")
    return path.resolve()


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout.strip()
