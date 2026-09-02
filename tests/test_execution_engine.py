import hashlib
import json
import threading

import pytest

from agentbus.execution.engine import DurableExecutionEngine, DurableExecutionError
from agentbus.execution.models import (
    AttemptStatus,
    FailureCategory,
    RunStatus,
    RetryPolicy,
    TaskExecutionResult,
    TaskStatus,
)
from agentbus.execution.state_store import (
    StateStore,
    ToolInvocationConflictError,
)
from agentbus.tools.protocol import ToolCapabilityName, ToolInvocationStatus
from agentbus.tools.runtime import build_managed_tool_runtime


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
    def __init__(self, mutation_log, store, *, approval_count=1):
        self.mutation_log = mutation_log
        self.store = store
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
            approval_target = self.mutation_log.parent / f"approval-{sequence}.txt"
            content = f"approval target {sequence}\n"
            approval_target.write_text(content, encoding="utf-8")
            runtime = build_managed_tool_runtime(
                workspace=self.mutation_log.parent,
                state_store=self.store,
            )
            try:
                call = runtime.prepare_model_call(
                    tool_name="filesystem.delete",
                    arguments={
                        "path": approval_target.name,
                        "expected_sha256": hashlib.sha256(
                            content.encode("utf-8")
                        ).hexdigest(),
                    },
                    expected_capabilities=(
                        ToolCapabilityName.FILESYSTEM_DELETE,
                    ),
                    run_id=context.run.run_id,
                    task_id=context.task.task_id,
                    caller_role="coder",
                    workspace_trusted=True,
                    provider_consented=True,
                    idempotency_key=f"approval-{sequence}",
                )
                pending = runtime.invoke(
                    call,
                    run_id=context.run.run_id,
                    task_id=context.task.task_id,
                    caller_role="coder",
                    workspace_trusted=True,
                    provider_consented=True,
                    invocation_id=f"tool-invocation-{sequence}",
                )
            finally:
                runtime.close()
            request = pending.approval_request
            assert request is not None
            return TaskExecutionResult(
                succeeded=False,
                summary=f"Tool approval {sequence} is pending",
                verifier_status="awaiting_tool_approval",
                metadata={
                    "_agentbus": {
                        "tool_approval_pending": {
                            "approval_id": request.approval_id,
                            "invocation_id": request.invocation_id,
                            "tool_name": request.tool_name,
                        },
                        "loop_continuation": {
                            "sequence": sequence,
                            "approval_id": request.approval_id,
                            "invocation_id": request.invocation_id,
                            "attempt_id": context.attempt_id,
                            "attempt_number": context.attempt_number,
                        },
                    }
                },
            )
        return success()


class BlockingResumeExecutor:
    def __init__(self):
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()

    def execute(self, context):
        assert context.continuation is not None
        with self._lock:
            self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=10)
        return success("continued once")


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
):
    mutation_log = tmp_path / "mutation.log"
    store = StateStore(tmp_path / "state.db")
    executor = ApprovalContinuationExecutor(mutation_log, store)
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
    approval_id = waiting.pending_approval_details[0]["approval_id"]
    engine.approve_task(
        "run-1",
        "step-1",
        "Approve exact synthetic invocation",
    )

    completed = engine.resume("run-1")

    assert completed.status == RunStatus.SUCCEEDED
    assert [call[0] for call in executor.calls] == [1, 1]
    assert executor.calls[0][1] is None
    assert executor.calls[1][1]["sequence"] == 1
    assert executor.calls[1][1]["approval_id"] == approval_id
    assert mutation_log.read_text(encoding="utf-8") == "patched-once\n"
    assert [
        attempt.status for attempt in store.list_attempts("run-1", "step-1")
    ] == [AttemptStatus.SUCCEEDED]


def test_two_tool_approvals_share_one_attempt_and_do_not_consume_retry_budget(
    tmp_path,
):
    mutation_log = tmp_path / "mutation.log"
    store = StateStore(tmp_path / "state.db")
    executor = ApprovalContinuationExecutor(
        mutation_log,
        store,
        approval_count=2,
    )
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(count=1))

    first_wait = engine.run_until_blocked("run-1")
    engine.approve_task("run-1", "step-1", "Approve first invocation")
    second_wait = engine.resume("run-1")
    engine.approve_task("run-1", "step-1", "Approve second invocation")
    completed = engine.resume("run-1")

    assert first_wait.status == RunStatus.WAITING_FOR_APPROVAL
    assert second_wait.status == RunStatus.WAITING_FOR_APPROVAL
    assert completed.status == RunStatus.SUCCEEDED
    assert [call[0] for call in executor.calls] == [1, 1, 1]
    assert [call[1]["sequence"] if call[1] else None for call in executor.calls] == [
        None,
        1,
        2,
    ]
    assert mutation_log.read_text(encoding="utf-8") == "patched-once\n"
    assert store.get_task("run-1", "step-1").current_attempt_count == 1
    assert len(store.list_attempts("run-1", "step-1")) == 1


def test_concurrent_resumes_are_fenced_to_one_continuation_executor(tmp_path):
    path = tmp_path / "state.db"
    mutation_log = tmp_path / "mutation.log"
    initial_store = StateStore(path)
    initial = DurableExecutionEngine(
        initial_store,
        ApprovalContinuationExecutor(mutation_log, initial_store),
    )
    create(initial, plan(count=1))
    waiting = initial.run_until_blocked("run-1")
    assert waiting.status == RunStatus.WAITING_FOR_APPROVAL
    initial.approve_task("run-1", "step-1", "Approve exact invocation")

    executor = BlockingResumeExecutor()
    first = DurableExecutionEngine(StateStore(path), executor)
    second = DurableExecutionEngine(StateStore(path), executor)
    first_result = {}

    def resume_first() -> None:
        first_result["report"] = first.resume("run-1")

    thread = threading.Thread(target=resume_first)
    thread.start()
    assert executor.entered.wait(timeout=10)

    fenced = second.resume("run-1")
    assert fenced.status == RunStatus.RUNNING
    assert executor.calls == 1

    executor.release.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert first_result["report"].status == RunStatus.SUCCEEDED
    assert executor.calls == 1
    assert len(initial_store.list_attempts("run-1", "step-1")) == 1
    assert any(
        event["event_type"] == "task_continuation_resume_fenced"
        for event in initial_store.list_events("run-1")
    )


def test_approving_one_task_keeps_run_paused_for_another_pending_approval(
    tmp_path,
):
    mutation_log = tmp_path / "mutation.log"
    store = StateStore(tmp_path / "state.db")
    executor = ApprovalContinuationExecutor(mutation_log, store)
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(count=2))

    waiting = engine.run_until_blocked("run-1")
    store.update_task_status(
        "run-1",
        "step-2",
        TaskStatus.READY,
        event_type="synthetic_sibling_ready",
    )
    store.update_task_status(
        "run-1",
        "step-2",
        TaskStatus.WAITING_FOR_APPROVAL,
        event_type="synthetic_sibling_approval_required",
    )

    first_approved = engine.approve_task(
        "run-1",
        "step-1",
        "Approve the exact synthetic tool invocation.",
    )

    assert waiting.status == RunStatus.WAITING_FOR_APPROVAL
    assert first_approved.status == RunStatus.WAITING_FOR_APPROVAL
    assert store.get_task("run-1", "step-1").status == TaskStatus.RUNNING
    assert store.get_task("run-1", "step-2").status == (
        TaskStatus.WAITING_FOR_APPROVAL
    )

    all_approved = engine.approve_task(
        "run-1",
        "step-2",
        "Approve the independent task.",
    )

    assert all_approved.status == RunStatus.RUNNING
    assert store.get_task("run-1", "step-2").status == TaskStatus.READY


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


def test_missing_tool_continuation_fails_truthfully_without_retry(tmp_path):
    pending_without_continuation = TaskExecutionResult(
        succeeded=False,
        summary="Tool approval is pending without reconstructable state.",
        verifier_status="awaiting_tool_approval",
        metadata={
            "_agentbus": {
                "tool_approval_pending": {
                    "approval_id": "missing-continuation-approval",
                    "invocation_id": "missing-continuation-invocation",
                    "tool_name": "test.execute",
                }
            }
        },
    )
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(
        store,
        ScriptedExecutor([pending_without_continuation]),
    )
    create(engine, plan(count=1))

    report = engine.run_until_blocked("run-1")
    attempt = store.list_attempts("run-1", "step-1")[0]

    assert report.status == RunStatus.FAILED
    assert report.failed_tasks == ["step-1"]
    assert report.attempts_per_task == {"step-1": 1}
    assert attempt.status == AttemptStatus.FAILED
    assert attempt.error_category == FailureCategory.RESUMABILITY_FAILURE
    assert store.get_task("run-1", "step-1").status == TaskStatus.FAILED
    assert "durable task continuation" in (report.failure_reason or "")


def test_waiting_attempt_does_not_report_stale_review_from_previous_attempt(tmp_path):
    store = StateStore(tmp_path / "state.db")
    engine = DurableExecutionEngine(store)
    create(engine, plan(count=1))
    store.update_run_status("run-1", RunStatus.RUNNING)
    store.update_task_status("run-1", "step-1", TaskStatus.READY)
    first = store.start_attempt("run-1", "step-1")
    store.complete_attempt(
        first.attempt_id,
        AttemptStatus.FAILED,
        error_category=FailureCategory.VERIFIER_FAILURE,
        error_message="First verifier failed.",
        metadata={
            "task_review": {
                "summary": "stale reviewer summary",
                "issues": [{"message": "stale issue"}],
                "required_fixes": ["stale fix"],
            }
        },
    )
    store.update_task_status("run-1", "step-1", TaskStatus.RETRYABLE)
    store.update_task_status("run-1", "step-1", TaskStatus.READY)
    second = store.start_attempt("run-1", "step-1")
    store.suspend_attempt_for_tool_approval(
        second.attempt_id,
        metadata={
            "_agentbus": {
                "tool_approval_pending": {
                    "approval_id": "approval-2",
                    "invocation_id": "invocation-2",
                    "tool_name": "test.execute",
                },
                "task_continuation": {
                    "attempt_id": second.attempt_id,
                    "attempt_number": 2,
                    "approval_id": "approval-2",
                    "invocation_id": "invocation-2",
                },
            }
        },
        approval_id="approval-2",
        invocation_id="invocation-2",
        tool_name="test.execute",
        observation_summary="Verifier is awaiting approval.",
    )

    report = engine.get_report("run-1")

    assert report.status == RunStatus.WAITING_FOR_APPROVAL
    assert report.reviewer_stage is None
    assert report.reviewer_summary is None
    assert report.reviewer_issues == []
    assert report.required_fixes == []


def test_legacy_interrupted_approval_fails_without_rewriting_attempt(tmp_path):
    mutation_log = tmp_path / "mutation.log"
    store = StateStore(tmp_path / "state.db")
    executor = ApprovalContinuationExecutor(mutation_log, store)
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(count=1))

    waiting = engine.run_until_blocked("run-1")
    attempt = store.list_attempts("run-1", "step-1")[0]
    metadata = dict(attempt.metadata)
    internal = dict(metadata["_agentbus"])
    internal.pop("loop_continuation")
    metadata["_agentbus"] = internal
    store.complete_attempt(
        attempt.attempt_id,
        AttemptStatus.INTERRUPTED,
        error_category=FailureCategory.INTERRUPTED,
        error_message="Legacy approval pause completed the attempt incorrectly.",
        metadata=metadata,
    )

    report = engine.approve_task(
        "run-1",
        "step-1",
        "Approve the exact legacy invocation.",
    )
    preserved = store.get_attempt(attempt.attempt_id)

    assert waiting.status == RunStatus.WAITING_FOR_APPROVAL
    assert report.status == RunStatus.FAILED
    assert report.failed_tasks == ["step-1"]
    assert preserved.status == AttemptStatus.INTERRUPTED
    assert preserved.error_category == FailureCategory.INTERRUPTED
    assert store.get_task("run-1", "step-1").status == TaskStatus.FAILED
    assert "safe durable continuation" in (report.failure_reason or "")
    assert report.task_failures == [
        {
            "task_id": "step-1",
            "category": "resumability_failure",
            "message": "Approved tool invocation has no safe durable continuation.",
        }
    ]
    assert executor.calls == [(1, None)]


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


def test_cancellation_while_waiting_prevents_tool_approval_and_resume(tmp_path):
    mutation_log = tmp_path / "mutation.log"
    store = StateStore(tmp_path / "state.db")
    executor = ApprovalContinuationExecutor(mutation_log, store)
    engine = DurableExecutionEngine(store, executor)
    create(engine, plan(count=1))

    waiting = engine.run_until_blocked("run-1")
    approval_id = waiting.pending_approval_details[0]["approval_id"]
    invocation_id = store.list_tool_approvals("run-1")[0].request.invocation_id

    cancelled = engine.cancel_run("run-1", "Cancel before exact approval")

    assert cancelled.status == RunStatus.CANCELLED
    assert store.get_task("run-1", "step-1").status == TaskStatus.CANCELLED
    attempt = store.list_attempts("run-1", "step-1")[0]
    assert attempt.status == AttemptStatus.INTERRUPTED
    assert attempt.error_category == FailureCategory.CANCELLED
    with pytest.raises(DurableExecutionError, match="not waiting for approval"):
        engine.approve_task("run-1", "step-1", "Too late")
    with pytest.raises(ToolInvocationConflictError, match="terminal or cancelled"):
        store.decide_tool_approval(
            "run-1",
            approval_id,
            disposition="approved",
            reason="Too late",
        )

    resumed = engine.resume("run-1")

    assert resumed.status == RunStatus.CANCELLED
    assert len(executor.calls) == 1
    assert store.get_tool_approval("run-1", approval_id).disposition is None
    assert store.get_tool_invocation("run-1", invocation_id).status == (
        ToolInvocationStatus.AWAITING_APPROVAL
    )
    assert (tmp_path / "approval-1.txt").is_file()
