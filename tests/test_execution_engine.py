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


class ApprovalContinuationExecutor:
    def __init__(self, mutation_log, *, approval_count=1):
        self.mutation_log = mutation_log
        self.approval_count = approval_count
        self.calls = []

    def execute(self, context):
        continuation = getattr(context, "continuation", None)
        self.calls.append((context.attempt_number, continuation))
        if continuation is None:
            self.mutation_log.write_text("patched-once\n", encoding="utf-8")
            sequence = 1
        else:
            sequence = int(continuation["sequence"]) + 1
        if sequence <= self.approval_count:
            return TaskExecutionResult(
                succeeded=False,
                summary=f"Tool approval {sequence} is pending",
                verifier_status="awaiting_tool_approval",
                metadata={
                    "_agentbus": {
                        "tool_approval_pending": {
                            "approval_id": f"tool-approval-{sequence}",
                            "invocation_id": f"tool-invocation-{sequence}",
                            "tool_name": "test.execute",
                        },
                        "loop_continuation": {"sequence": sequence},
                    }
                },
            )
        return success()


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
    mutation_log = tmp_path / "mutation.log"
    executor = ApprovalContinuationExecutor(mutation_log)
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(count=1))

    waiting = engine.run_until_blocked("run-1")

    assert waiting.status == RunStatus.WAITING_FOR_APPROVAL
    assert store.get_task("run-1", "step-1").status == (
        TaskStatus.WAITING_FOR_APPROVAL
    )
    assert store.list_attempts("run-1", "step-1")[0].status == (
        AttemptStatus.WAITING_FOR_APPROVAL
    )
    monkeypatch.setattr(
        store,
        "get_tool_approval",
        lambda run_id, approval_id: SimpleNamespace(disposition="approved"),
    )

    completed = engine.resume("run-1")

    assert completed.status == RunStatus.SUCCEEDED
    assert [call[0] for call in executor.calls] == [1, 1]
    assert executor.calls[0][1] is None
    assert executor.calls[1][1] == {"sequence": 1}
    assert mutation_log.read_text(encoding="utf-8") == "patched-once\n"
    assert [
        attempt.status for attempt in store.list_attempts("run-1", "step-1")
    ] == [AttemptStatus.SUCCEEDED]


def test_two_tool_approvals_share_one_attempt_and_do_not_consume_retry_budget(
    tmp_path,
    monkeypatch,
):
    mutation_log = tmp_path / "mutation.log"
    executor = ApprovalContinuationExecutor(mutation_log, approval_count=2)
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(count=1))
    monkeypatch.setattr(
        store,
        "get_tool_approval",
        lambda run_id, approval_id: SimpleNamespace(disposition="approved"),
    )

    first_wait = engine.run_until_blocked("run-1")
    second_wait = engine.resume("run-1")
    completed = engine.resume("run-1")

    assert first_wait.status == RunStatus.WAITING_FOR_APPROVAL
    assert second_wait.status == RunStatus.WAITING_FOR_APPROVAL
    assert completed.status == RunStatus.SUCCEEDED
    assert [call[0] for call in executor.calls] == [1, 1, 1]
    assert [call[1] for call in executor.calls] == [
        None,
        {"sequence": 1},
        {"sequence": 2},
    ]
    assert mutation_log.read_text(encoding="utf-8") == "patched-once\n"
    assert store.get_task("run-1", "step-1").current_attempt_count == 1
    assert len(store.list_attempts("run-1", "step-1")) == 1


def test_unrecoverable_attempt_exhaustion_persists_truthful_terminal_state(tmp_path):
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store, ScriptedExecutor())
    limited = plan(count=1)
    limited["steps"][0]["maximum_attempts"] = 1
    create(engine, limited)
    store.update_run_status("run-1", RunStatus.RUNNING)
    store.update_task_status("run-1", "step-1", TaskStatus.READY)
    store.update_task_status("run-1", "step-1", TaskStatus.RUNNING)
    attempt = store.create_attempt("run-1", "step-1")
    store.complete_attempt(
        attempt.attempt_id,
        AttemptStatus.INTERRUPTED,
        error_category=FailureCategory.INTERRUPTED,
        error_message="Simulated stale retry state.",
    )
    store.update_task_status("run-1", "step-1", TaskStatus.RETRYABLE)
    store.update_task_status("run-1", "step-1", TaskStatus.READY)

    report = engine.execute_next("run-1")

    assert report.status == RunStatus.FAILED
    assert store.get_task("run-1", "step-1").status == TaskStatus.FAILED
    assert store.get_task("run-1", "step-1").current_attempt_count == 1
    assert len(store.list_attempts("run-1", "step-1")) == 1
    assert "exhausted its maximum attempts" in (report.failure_reason or "")


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
