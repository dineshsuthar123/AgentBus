import json
from types import SimpleNamespace

import pytest

from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.models import (
    AttemptStatus,
    FailureCategory,
    RunStatus,
    RetryPolicy,
    TaskExecutionResult,
    TaskStatus,
)
from agentbus.execution.state_store import StateStore


def plan(*, risks=None, count=2):
    risks = risks or ["low"] * count
    return {
        "goal": "Durable feature",
        "steps": [
            {
                "id": f"step-{index + 1}",
                "title": f"Step {index + 1}",
                "description": f"Implement step {index + 1}",
                "risk": risks[index],
            }
            for index in range(count)
        ],
        "test_strategy": "Run tests",
        "done_criteria": ["All steps complete"],
    }


def success(summary="complete"):
    return TaskExecutionResult(
        succeeded=True,
        summary=summary,
        verifier_status="passed",
        reviewer_status="approved",
        changed_files=["app.py"],
    )


def failure(
    category=FailureCategory.POLICY_VIOLATION,
    *,
    retryable=False,
):
    return TaskExecutionResult(
        succeeded=False,
        summary="failed",
        failure_category=category,
        error_message="deterministic failure",
        retryable=retryable,
        verifier_status="failed",
        reviewer_status="rejected",
    )


class ScriptedExecutor:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = []

    def execute(self, context):
        self.calls.append((context.task.task_id, context.attempt_number))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return success(context.task.task_id)


def create(engine, planner_output=None):
    return engine.create_run(
        "Build a durable feature",
        planner_output or plan(),
        model="fake-model",
        workspace="workspace",
        run_id="run-1",
    )


def test_happy_path_completes_in_deterministic_order(tmp_path):
    executor = ScriptedExecutor()
    engine = DurableExecutionEngine(StateStore(tmp_path / "state.db"), executor)
    create(engine)

    report = engine.run_until_blocked("run-1")

    assert report.status == RunStatus.SUCCEEDED
    assert executor.calls == [("step-1", 1), ("step-2", 1)]
    assert report.successful_tasks == ["step-1", "step-2"]
    assert report.attempts_per_task == {"step-1": 1, "step-2": 1}


def test_process_recreation_resumes_without_reexecuting_success(tmp_path):
    path = tmp_path / "state.db"
    executor = ScriptedExecutor()
    first_engine = DurableExecutionEngine(StateStore(path), executor)
    create(first_engine)

    partial = first_engine.execute_next("run-1")
    assert partial.status == RunStatus.RUNNING
    assert executor.calls == [("step-1", 1)]

    second_engine = DurableExecutionEngine(StateStore(path), executor)
    report = second_engine.resume("run-1")
    second_engine.resume("run-1")

    assert report.status == RunStatus.SUCCEEDED
    assert executor.calls == [("step-1", 1), ("step-2", 1)]


def test_crash_after_attempt_start_recovers_and_preserves_terminal_task(tmp_path):
    path = tmp_path / "state.db"
    executor = ScriptedExecutor()
    first_engine = DurableExecutionEngine(StateStore(path), executor)
    create(first_engine)
    first_engine.execute_next("run-1")

    def crash_after_start(stage, context):
        assert stage == "after_attempt_started"
        assert context.task.task_id == "step-2"
        raise RuntimeError("simulated process interruption")

    first_engine.crash_hook = crash_after_start
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        first_engine.execute_next("run-1")

    restored_store = StateStore(path)
    report = DurableExecutionEngine(restored_store, executor).resume("run-1")
    attempts = restored_store.list_attempts("run-1", "step-2")

    assert report.status == RunStatus.SUCCEEDED
    assert executor.calls == [("step-1", 1), ("step-2", 2)]
    assert [attempt.status for attempt in attempts] == [
        AttemptStatus.INTERRUPTED,
        AttemptStatus.SUCCEEDED,
    ]
    assert restored_store.get_task("run-1", "step-1").current_attempt_count == 1
    assert any(
        event["event_type"] == "interrupted_attempt_recovered"
        for event in restored_store.list_events("run-1")
    )


def test_completed_attempt_is_promoted_after_completion_state_crash(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    engine = DurableExecutionEngine(store, ScriptedExecutor())
    create(engine, plan(count=1))
    store.update_run_status("run-1", RunStatus.RUNNING)
    store.update_task_status("run-1", "step-1", TaskStatus.READY)
    store.update_task_status("run-1", "step-1", TaskStatus.RUNNING)
    attempt = store.create_attempt("run-1", "step-1")
    store.complete_attempt(attempt.attempt_id, AttemptStatus.SUCCEEDED)

    executor = ScriptedExecutor()
    report = DurableExecutionEngine(StateStore(path), executor).resume("run-1")

    assert report.status == RunStatus.SUCCEEDED
    assert executor.calls == []


def test_persisted_retry_policy_is_used_after_process_recreation(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    first_engine = DurableExecutionEngine(
        store,
        ScriptedExecutor(),
        retry_policy=RetryPolicy(maximum_attempts=1),
    )
    create(first_engine, plan(count=1))
    store.update_run_status("run-1", RunStatus.RUNNING)
    store.update_task_status("run-1", "step-1", TaskStatus.READY)
    store.update_task_status("run-1", "step-1", TaskStatus.RUNNING)
    attempt = store.create_attempt("run-1", "step-1")
    store.complete_attempt(
        attempt.attempt_id,
        AttemptStatus.FAILED,
        error_category=FailureCategory.MODEL_OUTPUT_ERROR,
    )

    executor = ScriptedExecutor()
    report = DurableExecutionEngine(StateStore(path), executor).resume("run-1")

    assert report.status == RunStatus.FAILED
    assert executor.calls == []
    assert report.attempts_per_task == {"step-1": 1}


def test_retryable_failure_creates_separate_attempt(tmp_path):
    executor = ScriptedExecutor(
        [
            failure(FailureCategory.MODEL_OUTPUT_ERROR, retryable=True),
            success(),
        ]
    )
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(count=1))

    report = engine.run_until_blocked("run-1")

    assert report.status == RunStatus.SUCCEEDED
    assert executor.calls == [("step-1", 1), ("step-1", 2)]
    assert len(store.list_attempts("run-1", "step-1")) == 2


def test_malformed_executor_output_is_classified_and_retried(tmp_path):
    executor = ScriptedExecutor(
        [json.JSONDecodeError("malformed", "{", 1), success()]
    )
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(count=1))

    report = engine.run_until_blocked("run-1")

    assert report.status == RunStatus.SUCCEEDED
    assert executor.calls == [("step-1", 1), ("step-1", 2)]
    first_attempt = store.list_attempts("run-1", "step-1")[0]
    assert first_attempt.error_category == FailureCategory.MODEL_OUTPUT_ERROR


def test_retryable_failure_stops_when_attempt_limit_is_exhausted(tmp_path):
    executor = ScriptedExecutor(
        [
            failure(FailureCategory.MODEL_OUTPUT_ERROR, retryable=True),
            failure(FailureCategory.MODEL_OUTPUT_ERROR, retryable=True),
        ]
    )
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(count=1))

    report = engine.run_until_blocked("run-1")

    assert report.status == RunStatus.FAILED
    assert executor.calls == [("step-1", 1), ("step-1", 2)]
    assert len(store.list_attempts("run-1", "step-1")) == 2


def test_non_retryable_failure_blocks_dependents_and_fails_run(tmp_path):
    executor = ScriptedExecutor([failure()])
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store, executor)
    create(engine)

    report = engine.run_until_blocked("run-1")

    assert report.status == RunStatus.FAILED
    assert report.failed_tasks == ["step-1"]
    assert report.blocked_tasks == ["step-2"]
    assert executor.calls == [("step-1", 1)]
    assert "No valid progress" in report.failure_reason


def test_no_progress_state_fails_with_clear_reason(tmp_path):
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store, ScriptedExecutor())
    create(engine)
    store.update_task_status("run-1", "step-1", TaskStatus.BLOCKED)

    report = engine.run_until_blocked("run-1")

    assert report.status == RunStatus.FAILED
    assert "No valid progress" in report.failure_reason


def test_high_risk_task_waits_for_explicit_approval_then_resumes(tmp_path):
    executor = ScriptedExecutor()
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(risks=["high"], count=1))

    waiting = engine.run_until_blocked("run-1")

    assert waiting.status == RunStatus.WAITING_FOR_APPROVAL
    assert waiting.pending_approvals == ["step-1"]
    assert executor.calls == []

    approved = DurableExecutionEngine(StateStore(tmp_path / "state.db")).approve_task(
        "run-1", "step-1", "Reviewed by operator"
    )
    report = DurableExecutionEngine(StateStore(tmp_path / "state.db"), executor).resume(
        "run-1"
    )

    assert approved.status == RunStatus.RUNNING
    assert report.status == RunStatus.SUCCEEDED
    assert executor.calls == [("step-1", 1)]


def test_running_task_suspends_for_tool_approval_and_resumes_exact_attempt(
    tmp_path,
    monkeypatch,
):
    pending = TaskExecutionResult(
        succeeded=False,
        summary="Tool requires approval",
        failure_category=FailureCategory.POLICY_VIOLATION,
        error_message="Tool requires approval",
        retryable=False,
        metadata={
            "_agentbus": {
                "tool_approval_pending": {
                    "approval_id": "tool-approval-1",
                    "invocation_id": "tool-invocation-1",
                    "tool_name": "filesystem.delete",
                }
            }
        },
    )
    executor = ScriptedExecutor([pending, success()])
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(count=1))

    waiting = engine.run_until_blocked("run-1")

    assert waiting.status == RunStatus.WAITING_FOR_APPROVAL
    assert store.get_task("run-1", "step-1").status == (
        TaskStatus.WAITING_FOR_APPROVAL
    )
    assert store.list_attempts("run-1", "step-1")[0].status == (
        AttemptStatus.INTERRUPTED
    )
    monkeypatch.setattr(
        store,
        "get_tool_approval",
        lambda run_id, approval_id: SimpleNamespace(disposition="approved"),
    )

    completed = engine.resume("run-1")

    assert completed.status == RunStatus.SUCCEEDED
    assert executor.calls == [("step-1", 1), ("step-1", 2)]
    assert [
        attempt.status for attempt in store.list_attempts("run-1", "step-1")
    ] == [AttemptStatus.INTERRUPTED, AttemptStatus.SUCCEEDED]


def test_rejection_marks_task_and_blocks_dependents(tmp_path):
    executor = ScriptedExecutor()
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(risks=["high", "low"], count=2))
    engine.run_until_blocked("run-1")

    report = engine.reject_task("run-1", "step-1", "Not safe")

    assert report.status == RunStatus.FAILED
    assert report.failed_tasks == ["step-1"]
    assert report.blocked_tasks == ["step-2"]
    assert executor.calls == []


def test_cancellation_prevents_later_execution(tmp_path):
    executor = ScriptedExecutor()
    engine = DurableExecutionEngine(StateStore(tmp_path / "state.db"), executor)
    create(engine, plan(count=1))

    cancelled = engine.cancel_run("run-1", "User cancelled")
    resumed = engine.resume("run-1")

    assert cancelled.status == RunStatus.CANCELLED
    assert resumed.status == RunStatus.CANCELLED
    assert executor.calls == []
