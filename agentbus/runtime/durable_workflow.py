from __future__ import annotations

import inspect
import uuid
from pathlib import Path
from typing import Any

from agentbus.execution.cancellation import CancellationRequested, CancellationToken
from agentbus.execution.models import (
    FailureCategory,
    ExecutionArtifact,
    TaskExecutionContext,
    TaskExecutionResult,
)
from agentbus.models.errors import ModelCancellationError
from agentbus.models.router import model_request_context
from agentbus.git.repository import RepositoryChangeSet
from agentbus.runtime.loop import ManagedToolApprovalRequired
from agentbus.tools.runtime import ManagedToolRuntime


class MultiAgentTaskExecutor:
    """Adapts one durable graph task to the existing agent workflow."""

    def __init__(
        self,
        *,
        coder,
        verifier,
        reviewer,
        git_tools,
        git_repository,
        workspace: str | None = None,
        cancellation: CancellationToken | None = None,
        tool_runtime: ManagedToolRuntime | None = None,
    ):
        self.coder = coder
        self.verifier = verifier
        self.reviewer = reviewer
        self.git_tools = git_tools
        self.git_repository = git_repository
        repository_workspace = getattr(git_repository, "workspace", None)
        selected_workspace = workspace or repository_workspace
        if selected_workspace is None:
            raise ValueError("Durable task executor requires an explicit workspace.")
        self.workspace = Path(selected_workspace).expanduser().resolve()
        self.cancellation = cancellation
        self.tool_runtime = tool_runtime
        self._recovered_tool_runs: set[str] = set()

    def close(self) -> None:
        if self.tool_runtime is not None:
            self.tool_runtime.close()

    def execute(self, context: TaskExecutionContext) -> TaskExecutionResult:
        _drain_model_results(self.coder)
        _drain_model_results(self.reviewer)
        plan = self._task_plan(context)
        reviewer_feedback = self._previous_reviewer_feedback(context)
        before = self._snapshot()
        coder_summary = ""
        verifier_result: dict[str, Any] | None = None
        reviewer_result: dict[str, Any] | None = None
        try:
            self._recover_tool_runtime(context.run.run_id)
            with model_request_context(
                run_id=context.run.run_id,
                task_id=context.task.task_id,
                cancellation=self.cancellation,
            ):
                self._checkpoint("before-coder")
                coder_arguments = {
                    "user_task": context.run.original_task,
                    "plan": plan,
                    "reviewer_feedback": reviewer_feedback,
                    "cancellation": self.cancellation,
                    "tool_runtime": self.tool_runtime,
                    "run_id": context.run.run_id,
                    "task_id": context.task.task_id,
                    "workspace_trusted": True,
                    "provider_consented": True,
                    "policy_context": {
                        "attempt_number": context.attempt_number,
                        "assigned_role": context.task.assigned_role,
                        "planned_capabilities": list(
                            context.task.metadata.get(
                                "required_capabilities",
                                [],
                            )
                        ),
                    },
                }
                coder_summary = self.coder.execute(
                    **_supported_arguments(
                        self.coder.execute,
                        coder_arguments,
                    )
                )
                self._checkpoint("after-coder")
                verifier_result = self.verifier.verify(
                    **_supported_arguments(
                        self.verifier.verify,
                        {
                            "tool_runtime": self.tool_runtime,
                            "run_id": context.run.run_id,
                            "task_id": context.task.task_id,
                            "invocation_key": (
                                f"attempt-{context.attempt_number}"
                            ),
                            "workspace_trusted": True,
                            "provider_consented": True,
                        },
                    )
                )
                self._checkpoint("after-verifier")
                changed_files = self._changed_since(before)
                changes = self._change_set(changed_files)
                task_diff = self._task_diff(changes)
                self._checkpoint("before-task-review")
                reviewer_result = self._review_task(
                    context,
                    plan,
                    changes,
                    task_diff,
                    coder_summary,
                    verifier_result,
                )
                self._checkpoint("after-task-review")
        except ManagedToolApprovalRequired as exc:
            return self._approval_pending_result(
                context,
                before,
                exc,
                coder_summary=coder_summary,
            )
        except (CancellationRequested, ModelCancellationError):
            return self._cancelled_result(
                context,
                before,
                coder_summary=coder_summary,
                verifier_result=verifier_result,
            )
        assert verifier_result is not None
        assert reviewer_result is not None
        verifier_status = "passed" if verifier_result.get("passed") else "failed"
        metadata = {
            "task_review": {
                "approved": bool(reviewer_result.get("approved")),
                "issues": reviewer_result.get("issues", []),
                "summary": reviewer_result.get("summary", ""),
                "required_fixes": reviewer_result.get("required_fixes", []),
            },
            "verifier": {
                "passed": bool(verifier_result.get("passed")),
                "command": verifier_result.get("command", []),
                "exit_code": verifier_result.get("exit_code"),
                "reason": verifier_result.get("reason"),
                "artifact_suppression_active": bool(
                    verifier_result.get("artifact_suppression_active")
                ),
                "pytest_cache_disabled": bool(
                    verifier_result.get("pytest_cache_disabled")
                ),
            },
            "artifact_hygiene": changes.to_metadata(),
            # Retained for retry feedback compatibility with persisted attempts.
            "reviewer_feedback": {
                "approved": bool(reviewer_result.get("approved")),
                "issues": reviewer_result.get("issues", []),
                "summary": reviewer_result.get("summary", ""),
                "required_fixes": reviewer_result.get("required_fixes", []),
            },
            "model_requests": [
                *(_drain_model_results(self.coder)),
                *(_drain_model_results(self.reviewer)),
            ],
        }
        generated = set(changes.generated_files)
        ignored = set(changes.ignored_files)
        tracked_generated = set(changes.tracked_generated_files)
        artifacts = [
            ExecutionArtifact(
                artifact_id=uuid.uuid4().hex,
                run_id=context.run.run_id,
                task_id=context.task.task_id,
                artifact_type="workspace_file",
                identifier=path,
                metadata={
                    "attempt_number": context.attempt_number,
                    "generated": path in generated,
                    "ignored": path in ignored,
                    "tracked_generated": path in tracked_generated,
                    "review_eligible": path in set(changes.review_files),
                    "commit_eligible": path in set(changes.commit_files),
                },
            )
            for path in changed_files
        ]

        if not verifier_result.get("passed"):
            return TaskExecutionResult(
                succeeded=False,
                summary=f"Verification failed after coder output: {coder_summary}",
                failure_category=FailureCategory.VERIFIER_FAILURE,
                error_message="The verifier command did not pass.",
                retryable=True,
                artifacts=artifacts,
                verifier_status=verifier_status,
                changed_files=changed_files,
                metadata=metadata,
            )

        if not reviewer_result.get("approved"):
            return TaskExecutionResult(
                succeeded=False,
                summary=reviewer_result.get("summary", "Reviewer rejected the task."),
                failure_category=FailureCategory.REVIEWER_REJECTION,
                error_message="The reviewer requested corrections.",
                retryable=True,
                artifacts=artifacts,
                verifier_status=verifier_status,
                changed_files=changed_files,
                metadata=metadata,
            )

        return TaskExecutionResult(
            succeeded=True,
            summary=coder_summary,
            artifacts=artifacts,
            verifier_status=verifier_status,
            changed_files=changed_files,
            metadata=metadata,
        )

    def _approval_pending_result(
        self,
        context: TaskExecutionContext,
        before: dict[str, str],
        approval: ManagedToolApprovalRequired,
        *,
        coder_summary: str,
    ) -> TaskExecutionResult:
        changed_files = self._changed_since(before)
        changes = self._change_set(changed_files)
        review_files = set(changes.review_files)
        commit_files = set(changes.commit_files)
        generated = set(changes.generated_files)
        ignored = set(changes.ignored_files)
        tracked_generated = set(changes.tracked_generated_files)
        artifacts = [
            ExecutionArtifact(
                artifact_id=uuid.uuid4().hex,
                run_id=context.run.run_id,
                task_id=context.task.task_id,
                artifact_type="workspace_file",
                identifier=path,
                metadata={
                    "attempt_number": context.attempt_number,
                    "generated": path in generated,
                    "ignored": path in ignored,
                    "tracked_generated": path in tracked_generated,
                    "review_eligible": path in review_files,
                    "commit_eligible": path in commit_files,
                },
            )
            for path in changed_files
        ]
        pending = {
            "approval_id": approval.approval_id,
            "invocation_id": approval.invocation_id,
            "tool_name": approval.tool_name,
        }
        return TaskExecutionResult(
            succeeded=False,
            summary=str(approval),
            artifacts=artifacts,
            failure_category=FailureCategory.POLICY_VIOLATION,
            error_message=str(approval),
            retryable=False,
            verifier_status="awaiting_tool_approval",
            changed_files=changed_files,
            metadata={
                "artifact_hygiene": changes.to_metadata(),
                "coder_summary": coder_summary,
                "tool_approval": pending,
                "_agentbus": {"tool_approval_pending": pending},
                "model_requests": _drain_model_results(self.coder),
            },
        )

    def _recover_tool_runtime(self, run_id: str) -> None:
        if self.tool_runtime is None or run_id in self._recovered_tool_runs:
            return
        self.tool_runtime.recover_run(run_id)
        self._recovered_tool_runs.add(run_id)

    def _cancelled_result(
        self,
        context: TaskExecutionContext,
        before: dict[str, str],
        *,
        coder_summary: str,
        verifier_result: dict[str, Any] | None,
    ) -> TaskExecutionResult:
        changed_files = self._changed_since(before)
        changes = self._change_set(changed_files)
        generated = set(changes.generated_files)
        ignored = set(changes.ignored_files)
        tracked_generated = set(changes.tracked_generated_files)
        review_files = set(changes.review_files)
        commit_files = set(changes.commit_files)
        artifacts = [
            ExecutionArtifact(
                artifact_id=uuid.uuid4().hex,
                run_id=context.run.run_id,
                task_id=context.task.task_id,
                artifact_type="workspace_file",
                identifier=path,
                metadata={
                    "attempt_number": context.attempt_number,
                    "generated": path in generated,
                    "ignored": path in ignored,
                    "tracked_generated": path in tracked_generated,
                    "review_eligible": path in review_files,
                    "commit_eligible": path in commit_files,
                },
            )
            for path in changed_files
        ]
        state = self.cancellation.snapshot() if self.cancellation is not None else None
        return TaskExecutionResult(
            succeeded=False,
            summary="Task stopped after cancellation was requested.",
            artifacts=artifacts,
            failure_category=FailureCategory.CANCELLED,
            error_message="Execution stopped cooperatively at a safe checkpoint.",
            retryable=False,
            verifier_status=(
                "passed"
                if verifier_result and verifier_result.get("passed")
                else "cancelled"
            ),
            changed_files=changed_files,
            metadata={
                "artifact_hygiene": changes.to_metadata(),
                "coder_summary": coder_summary,
                "cancellation": (
                    {
                        "requested_at": state.requested_at.isoformat()
                        if state and state.requested_at
                        else None,
                        "acknowledgement_source": (
                            state.acknowledgement_source if state else None
                        ),
                        "acknowledgement_stage": (
                            state.acknowledgement_stage if state else None
                        ),
                    }
                ),
                "model_requests": [
                    *(_drain_model_results(self.coder)),
                    *(_drain_model_results(self.reviewer)),
                ],
            },
        )

    def _checkpoint(self, stage: str) -> None:
        if self.cancellation is not None:
            self.cancellation.checkpoint(
                "durable-task-executor",
                stage=stage,
            )

    def _task_plan(self, context: TaskExecutionContext) -> dict[str, Any]:
        task = context.task
        return {
            "goal": context.run.planner_output.get("goal", context.run.original_task),
            "steps": [
                {
                    "id": task.task_id,
                    "title": task.title,
                    "description": task.description,
                    "risk": task.risk.value,
                    "dependencies": task.dependency_ids,
                    "assigned_role": task.assigned_role,
                    "expected_outputs": task.expected_outputs,
                    "done_criteria": task.done_criteria,
                    "required_capabilities": list(
                        task.metadata.get("required_capabilities", [])
                    ),
                }
            ],
            "test_strategy": context.run.planner_output.get(
                "test_strategy", "Run the detected test command."
            ),
            "done_criteria": task.done_criteria,
        }

    @staticmethod
    def _previous_reviewer_feedback(
        context: TaskExecutionContext,
    ) -> dict[str, Any] | None:
        if not context.previous_attempts:
            return None
        feedback = context.previous_attempts[-1].metadata.get("reviewer_feedback")
        return feedback if isinstance(feedback, dict) else None

    def _changed_files(self) -> list[str]:
        if not self.git_repository.is_git_repo():
            return []
        return self.git_repository.changed_files()

    def _snapshot(self) -> dict[str, str]:
        snapshot = getattr(self.git_repository, "worktree_snapshot", None)
        if snapshot is None:
            return {}
        return snapshot()

    def _changed_since(self, before: dict[str, str]) -> list[str]:
        changed_since = getattr(self.git_repository, "changed_since", None)
        if changed_since is None:
            return self._changed_files()
        return changed_since(before)

    def _change_set(self, changed_files: list[str]) -> RepositoryChangeSet:
        change_set = getattr(self.git_repository, "change_set", None)
        if change_set is not None:
            return change_set(changed_files)
        return RepositoryChangeSet(
            changed_files=changed_files,
            relevant_files=changed_files,
            generated_files=[],
            ignored_files=[],
            tracked_generated_files=[],
            review_files=changed_files,
            review_excluded_files=[],
            commit_files=changed_files,
        )

    def _task_diff(self, changes: RepositoryChangeSet) -> str:
        review_diff = getattr(self.git_repository, "review_diff", None)
        if review_diff is not None:
            return review_diff(max_chars=30_000, paths=changes.changed_files)
        full_diff = getattr(self.git_repository, "full_diff", None)
        if full_diff is None:
            return self.git_tools.git_diff()
        return full_diff(max_chars=30_000, paths=changes.review_files)

    def _review_task(
        self,
        context: TaskExecutionContext,
        plan: dict[str, Any],
        changes: RepositoryChangeSet,
        task_diff: str,
        coder_summary: str,
        verifier_result: dict[str, Any],
    ) -> dict[str, Any]:
        review_task = getattr(self.reviewer, "review_task", None)
        if review_task is not None:
            arguments = {
                "original_task": context.run.original_task,
                "task_spec": plan["steps"][0],
                "expected_outputs": context.task.expected_outputs,
                "artifacts": changes.review_files,
                "task_diff": task_diff,
                "coder_summary": coder_summary,
                "verifier_result": verifier_result,
                "generated_artifacts": changes.generated_files,
                "ignored_files": changes.ignored_files,
                "tracked_generated_artifacts": changes.tracked_generated_files,
            }
            return review_task(**_supported_arguments(review_task, arguments))
        return self.reviewer.review(
            user_task=context.run.original_task,
            plan=plan,
            git_diff=task_diff,
            test_output=verifier_result.get("output"),
        )


def _drain_model_results(agent) -> list[dict[str, Any]]:
    model = getattr(agent, "model", None)
    drain = getattr(model, "drain_results", None)
    if drain is None:
        return []
    return [result.event_metadata() for result in drain()]


def _supported_arguments(callable_object, arguments: dict[str, Any]) -> dict[str, Any]:
    parameters = inspect.signature(callable_object).parameters.values()
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return arguments
    supported = {parameter.name for parameter in parameters}
    return {name: value for name, value in arguments.items() if name in supported}
