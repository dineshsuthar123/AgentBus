from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agentbus.agents.coder import CoderAgent
from agentbus.config import AgentBusConfig
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.models import AttemptStatus, RunStatus, TaskStatus
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepository
from agentbus.replay import CheckpointManager
from agentbus.runtime.durable_workflow import MultiAgentTaskExecutor
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools.git_tools import GitTools
from agentbus.tools.protocol import ToolInvocationStatus
from agentbus.tools.runtime import build_managed_tool_runtime
from agentbus.trace import RuntimeTrace, TraceSpanType, TraceStatus


PLAN = {
    "goal": "Patch one module and run repository verification",
    "steps": [
        {
            "id": "step-1",
            "title": "Patch and verify",
            "description": "Patch module.py and run both repository checks.",
            "dependencies": [],
            "risk": "low",
            "maximum_attempts": 2,
            "required_capabilities": [
                "filesystem.write",
                "test.execute",
                "process.execute",
            ],
            "expected_outputs": ["module.py"],
            "done_criteria": ["Both repository checks pass."],
        }
    ],
    "test_strategy": "Use offline executable aliases.",
    "done_criteria": ["The patch and both checks complete."],
}


class ContinuationModel:
    actions: list[str] = []
    terminal_decisions = 0

    def generate_json(self, prompt: str, **kwargs):
        history = prompt.split("Previous observations:", 1)[1].split(
            "Managed tool catalog:", 1
        )[0]
        if '"idempotency_key": "patch-once"' not in history:
            action = {
                "action": "tool_call",
                "tool_call": {
                    "tool_name": "filesystem.patch",
                    "arguments": {
                        "path": "module.py",
                        "expected": "VALUE = 1",
                        "replacement": "VALUE = 2",
                        "expected_occurrences": 1,
                    },
                    "expected_capabilities": ["filesystem.write"],
                    "idempotency_key": "patch-once",
                },
            }
        elif '"idempotency_key": "maven-test"' not in history:
            action = {
                "action": "tool_call",
                "tool_call": {
                    "tool_name": "test.execute",
                    "arguments": {
                        "executable": "mvn",
                        "arguments": ["test"],
                        "working_directory": ".",
                    },
                    "expected_capabilities": [
                        "test.execute",
                        "process.execute",
                    ],
                    "idempotency_key": "maven-test",
                },
            }
        elif '"idempotency_key": "repository-hook"' not in history:
            action = {
                "action": "tool_call",
                "tool_call": {
                    "tool_name": "process.execute",
                    "arguments": {
                        "executable": "repo-hook",
                        "arguments": [],
                        "working_directory": ".",
                    },
                    "expected_capabilities": ["process.execute"],
                    "idempotency_key": "repository-hook",
                },
            }
        else:
            if "Terminal decision turn:" in prompt:
                type(self).terminal_decisions += 1
            action = {
                "action": "finish",
                "summary": "Patch and both approved repository checks completed.",
            }
        key = (
            action.get("tool_call", {}).get("idempotency_key")
            if action["action"] == "tool_call"
            else "finish"
        )
        self.actions.append(key)
        return action


class PassingVerifier:
    def __init__(self, store: StateStore):
        self.store = store
        self.calls = 0

    def verify(self, *, run_id: str, **kwargs):
        self.calls += 1
        records = self.store.list_tool_invocations(run_id)
        assert [record.status for record in records[-2:]] == [
            ToolInvocationStatus.SUCCEEDED,
            ToolInvocationStatus.SUCCEEDED,
        ]
        return {
            "passed": True,
            "status": "passed",
            "command": ["offline", "verify"],
            "exit_code": 0,
            "output": "offline verification passed",
            "reason": "deterministic fixture",
        }


class PassingReviewer:
    def __init__(self):
        self.calls = 0
        self.artifacts: list[str] = []
        self.task_diff = ""

    def review_task(self, *, artifacts, task_diff, **kwargs):
        self.calls += 1
        self.artifacts = list(artifacts)
        self.task_diff = task_diff
        return {
            "approved": True,
            "issues": [],
            "summary": "The resumed task is complete.",
            "required_fixes": [],
        }


def test_exact_tool_approvals_resume_one_attempt_across_process_restarts(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "workspace")
    state_path = tmp_path / "state.db"
    config = AgentBusConfig(
        provider_name="deterministic",
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=3,
    )
    ContinuationModel.actions = []
    ContinuationModel.terminal_decisions = 0
    verifier_calls: list[PassingVerifier] = []
    reviewer_calls: list[PassingReviewer] = []
    traces: list[RuntimeTrace] = []

    initial_store = StateStore(state_path)
    DurableExecutionEngine(initial_store).create_run(
        "Patch the module and run the two repository checks.",
        PLAN,
        model="offline-continuation-model",
        workspace=str(workspace),
        run_id="run-1",
    )

    def new_engine() -> tuple[DurableExecutionEngine, StateStore]:
        store = StateStore(state_path)
        cancellations = CancellationRegistry(store)
        trace = RuntimeTrace.open(
            store,
            "run-1",
            object_root=tmp_path / "trace-objects",
            workspace=workspace,
        )
        traces.append(trace)
        verifier = PassingVerifier(store)
        reviewer = PassingReviewer()
        verifier_calls.append(verifier)
        reviewer_calls.append(reviewer)
        runtime = build_managed_tool_runtime(
            workspace=workspace,
            state_store=store,
            cancellation_registry=cancellations,
            executable_catalog=ExecutableCatalog(
                {
                    "python": sys.executable,
                    "mvn": (
                        sys.executable,
                        "-c",
                        "print('BUILD SUCCESS')",
                    ),
                    "repo-hook": (
                        sys.executable,
                        "-c",
                        "print('REPOSITORY HOOK SUCCESS')",
                    ),
                }
            ),
            runtime_trace=trace,
        )
        executor = MultiAgentTaskExecutor(
            coder=CoderAgent(config=config, model=ContinuationModel()),
            verifier=verifier,
            reviewer=reviewer,
            git_tools=GitTools(str(workspace)),
            git_repository=GitRepository(str(workspace)),
            workspace=str(workspace),
            tool_runtime=runtime,
            runtime_trace=trace,
        )
        return (
            DurableExecutionEngine(
                store,
                executor,
                cancellation_registry=cancellations,
                runtime_trace=trace,
            ),
            store,
        )

    first_engine, first_store = new_engine()
    first_wait = first_engine.run_until_blocked("run-1")
    first_attempt = first_store.list_attempts("run-1", "step-1")[0]
    first_approval = first_store.list_tool_approvals("run-1")[-1]

    assert first_wait.status == RunStatus.WAITING_FOR_APPROVAL
    assert first_attempt.status == AttemptStatus.WAITING_FOR_APPROVAL
    assert first_attempt.attempt_number == 1
    assert first_wait.pending_approval_details[0]["approval_kind"] == "tool"
    assert first_wait.pending_approval_details[0]["tool_name"] == "test.execute"
    assert (workspace / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    first_trace = traces[-1].snapshot()
    suspended_spans = [
        span
        for span in first_trace.spans
        if span.span_type == TraceSpanType.TASK
        and span.attributes.get("suspended_for_tool_approval")
    ]
    assert len(suspended_spans) == 1
    assert suspended_spans[0].status == TraceStatus.SUCCEEDED
    first_engine.close()

    second_engine, second_store = new_engine()
    approved_first = second_engine.approve_task(
        "run-1",
        "step-1",
        "Approve the exact offline Maven invocation.",
    )
    assert approved_first.status == RunStatus.RUNNING
    assert second_store.list_attempts("run-1", "step-1")[0].attempt_id == (
        first_attempt.attempt_id
    )
    second_wait = second_engine.resume("run-1")
    second_attempt = second_store.list_attempts("run-1", "step-1")[0]
    second_approval = second_store.list_tool_approvals("run-1")[-1]

    assert second_wait.status == RunStatus.WAITING_FOR_APPROVAL
    assert second_attempt.status == AttemptStatus.WAITING_FOR_APPROVAL
    assert second_attempt.attempt_id == first_attempt.attempt_id
    assert second_attempt.attempt_number == 1
    assert second_approval.approval_id != first_approval.approval_id
    assert second_approval.request.invocation_id != first_approval.request.invocation_id
    assert second_wait.pending_approval_details[0]["tool_name"] == "process.execute"
    second_engine.close()

    third_engine, third_store = new_engine()
    third_engine.approve_task(
        "run-1",
        "step-1",
        "Approve the exact offline repository hook invocation.",
    )
    completed = third_engine.resume("run-1")
    attempts = third_store.list_attempts("run-1", "step-1")
    invocations = third_store.list_tool_invocations("run-1")
    approvals = third_store.list_tool_approvals("run-1")
    events = third_store.list_events("run-1")

    assert completed.status == RunStatus.SUCCEEDED
    assert completed.attempts_per_task == {"step-1": 1}
    assert completed.changed_files == ["module.py"]
    assert len(attempts) == 1
    assert attempts[0].attempt_id == first_attempt.attempt_id
    assert attempts[0].status == AttemptStatus.SUCCEEDED
    assert third_store.get_task("run-1", "step-1").status == TaskStatus.SUCCEEDED
    assert [record.tool_name for record in invocations] == [
        "filesystem.patch",
        "test.execute",
        "process.execute",
    ]
    assert all(
        record.status == ToolInvocationStatus.SUCCEEDED for record in invocations
    )
    assert len(approvals) == 2
    assert {approval.disposition for approval in approvals} == {"approved"}
    assert ContinuationModel.actions == [
        "patch-once",
        "maven-test",
        "repository-hook",
        "finish",
    ]
    assert ContinuationModel.terminal_decisions == 1
    assert sum(verifier.calls for verifier in verifier_calls) == 1
    assert sum(reviewer.calls for reviewer in reviewer_calls) == 1
    final_reviewer = next(reviewer for reviewer in reviewer_calls if reviewer.calls)
    assert final_reviewer.artifacts == ["module.py"]
    assert "VALUE = 2" in final_reviewer.task_diff
    assert sum(
        event["event_type"] == "task_attempt_started" for event in events
    ) == 1
    assert sum(
        event["event_type"] == "task_attempt_resumed_after_tool_approval"
        for event in events
    ) == 2
    assert not any(
        event["event_type"] in {
            "task_attempt_failed",
            "task_attempt_interrupted",
        }
        for event in events
    )
    trace = traces[-1].snapshot()
    approval_checkpoints = [
        checkpoint
        for checkpoint in trace.checkpoints
        if checkpoint.label.startswith("tool-approval-")
    ]
    assert [checkpoint.label for checkpoint in approval_checkpoints] == [
        "tool-approval-requested-step-1",
        "tool-approval-approved-step-1",
        "tool-approval-requested-step-1",
        "tool-approval-approved-step-1",
    ]
    checkpoint_manager = CheckpointManager(traces[-1].object_store)
    approval_states = [
        checkpoint_manager.load_state(checkpoint).durable_state
        for checkpoint in approval_checkpoints
    ]
    assert {state["attempt_id"] for state in approval_states} == {
        first_attempt.attempt_id
    }
    assert {state["attempt_number"] for state in approval_states} == {1}
    task_spans = [
        span for span in trace.spans if span.span_type == TraceSpanType.TASK
    ]
    assert len(task_spans) == 3
    assert all(span.status == TraceStatus.SUCCEEDED for span in task_spans)
    third_engine.close()


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "AgentBus Tests")
    _git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(path, "add", "module.py")
    _git(path, "commit", "-q", "-m", "test: initialize fixture")
    return path.resolve()


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout.strip()
