from __future__ import annotations

from typing import Any

from agentbus.execution.models import (
    FailureCategory,
    TaskExecutionContext,
    TaskExecutionResult,
)


class MultiAgentTaskExecutor:
    """Adapts one durable graph task to the existing agent workflow."""

    def __init__(self, *, coder, verifier, reviewer, git_tools, git_repository):
        self.coder = coder
        self.verifier = verifier
        self.reviewer = reviewer
        self.git_tools = git_tools
        self.git_repository = git_repository

    def execute(self, context: TaskExecutionContext) -> TaskExecutionResult:
        plan = self._task_plan(context)
        reviewer_feedback = self._previous_reviewer_feedback(context)
        coder_summary = self.coder.execute(
            context.run.original_task,
            plan,
            reviewer_feedback=reviewer_feedback,
        )
        verifier_result = self.verifier.verify()
        reviewer_result = self.reviewer.review(
            user_task=context.run.original_task,
            plan=plan,
            git_diff=self.git_tools.git_diff(),
            test_output=verifier_result.get("output"),
        )
        verifier_status = "passed" if verifier_result.get("passed") else "failed"
        reviewer_status = "approved" if reviewer_result.get("approved") else "rejected"
        changed_files = self._changed_files()
        metadata = {
            "reviewer_feedback": {
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
            },
        }

        if not verifier_result.get("passed"):
            return TaskExecutionResult(
                succeeded=False,
                summary=f"Verification failed after coder output: {coder_summary}",
                failure_category=FailureCategory.VERIFIER_FAILURE,
                error_message="The verifier command did not pass.",
                retryable=True,
                verifier_status=verifier_status,
                reviewer_status=reviewer_status,
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
                verifier_status=verifier_status,
                reviewer_status=reviewer_status,
                changed_files=changed_files,
                metadata=metadata,
            )

        return TaskExecutionResult(
            succeeded=True,
            summary=coder_summary,
            verifier_status=verifier_status,
            reviewer_status=reviewer_status,
            changed_files=changed_files,
            metadata=metadata,
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
