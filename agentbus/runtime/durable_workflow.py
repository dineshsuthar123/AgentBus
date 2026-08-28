from __future__ import annotations

import hashlib
import inspect
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agentbus.execution.cancellation import CancellationRequested, CancellationToken
from agentbus.execution.models import (
    FailureCategory,
    ExecutionArtifact,
    TaskExecutionKind,
    TaskExecutionContext,
    TaskExecutionResult,
)
from agentbus.models.errors import ModelCancellationError
from agentbus.models.router import model_request_context
from agentbus.git.repository import RepositoryBaselineMismatch, RepositoryChangeSet
from agentbus.runtime.intelligence import PlannerIntelligenceContext
from agentbus.runtime.intelligence_guidance import (
    build_coder_intelligence,
    build_reviewer_intelligence,
)
from agentbus.runtime.loop import (
    ManagedToolApprovalRequired,
    ManagedToolContinuationError,
    PlannedCapabilityMismatchError,
    StepBudgetExhaustedError,
    validate_exact_tool_approval,
)
from agentbus.runtime.schemas import (
    RepositoryBaseline,
    RetryDiagnostics,
    RetryEvidence,
    RetryFeedback,
    TaskRepositoryBaselines,
    VerifierContinuation,
)
from agentbus.security.redaction import (
    redact_diagnostic_text,
    sanitize_diagnostic_json,
    sanitize_json,
)
from agentbus.trace import (
    RuntimeTrace,
    TraceArtifactReference,
    TraceFailure,
    TraceSpanType,
    TraceStatus,
)
from agentbus.tools.runtime import ManagedToolRuntime
from agentbus.tools.protocol import sha256_json


_ANALYSIS_SUMMARY_MAX_CHARS = 16_000
_RETRY_DIAGNOSTIC_MAX_CHARS = 6_000
_RETRYABLE_FEEDBACK_CATEGORIES = {
    FailureCategory.COMMAND_FAILURE,
    FailureCategory.INTERRUPTED,
    FailureCategory.MODEL_OUTPUT_ERROR,
    FailureCategory.MODEL_PROVIDER_ERROR,
    FailureCategory.MODEL_TRANSPORT_ERROR,
    FailureCategory.REVIEWER_REJECTION,
    FailureCategory.TOOL_VALIDATION_ERROR,
    FailureCategory.UNKNOWN,
    FailureCategory.VERIFIER_FAILURE,
}
_FAILURE_LINE_PATTERN = re.compile(
    r"(?:FAILED|ERROR|AssertionError|Exception|Traceback|expected\b.*\bgot|Tests run:)",
    re.IGNORECASE,
)


class RetryEvidenceSourceMismatch(ManagedToolContinuationError):
    """Signals that persisted retry evidence no longer matches its source."""


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
        runtime_trace: RuntimeTrace | None = None,
        worker_id: str | None = None,
        intelligence_context: PlannerIntelligenceContext | None = None,
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
        self.runtime_trace = runtime_trace
        self.worker_id = worker_id
        self.intelligence_context = intelligence_context
        self._recovered_tool_runs: set[str] = set()

    def close(self) -> None:
        if self.tool_runtime is not None:
            self.tool_runtime.close()

    def prepare_attempt(self, context: TaskExecutionContext) -> dict[str, Any]:
        """Capture distinct immutable task and attempt baselines before execution."""
        existing = context.attempt_metadata.get("repository_baselines")
        if isinstance(existing, dict):
            baselines = self._validate_repository_baselines(context, existing)
            updates = {"repository_baselines": baselines.model_dump(mode="json")}
            raw_feedback = context.attempt_metadata.get("retry_feedback")
            if isinstance(raw_feedback, dict):
                feedback = self._validate_retry_feedback(context, raw_feedback)
                updates["retry_feedback"] = feedback.model_dump(mode="json")
            elif context.previous_attempts and context.continuation is None:
                raise RetryEvidenceSourceMismatch(
                    "Retry evidence is missing from an already checkpointed retry attempt."
                )
            return updates

        previous_baselines: list[TaskRepositoryBaselines] = []
        for previous in sorted(
            context.previous_attempts,
            key=lambda item: item.attempt_number,
        ):
            raw = previous.metadata.get("repository_baselines")
            if not isinstance(raw, dict):
                continue
            try:
                previous_baselines.append(TaskRepositoryBaselines.model_validate(raw))
            except ValidationError as exc:
                raise RepositoryBaselineMismatch(
                    "A previous durable attempt contains an invalid repository baseline."
                ) from exc

        if context.previous_attempts and not previous_baselines:
            raise RepositoryBaselineMismatch(
                "A retry cannot reconstruct the original task baseline safely."
            )
        if previous_baselines:
            task_identity = previous_baselines[0].task.identity_sha256
            if any(
                item.task.identity_sha256 != task_identity
                for item in previous_baselines[1:]
            ):
                raise RepositoryBaselineMismatch(
                    "Previous retry attempts disagree about the original task baseline."
                )
            task_baseline = previous_baselines[0].task
            task_started_attempt_id = previous_baselines[0].task_started_attempt_id
            task_started_attempt_number = (
                previous_baselines[0].task_started_attempt_number
            )
        else:
            task_baseline = self._capture_repository_baseline()
            task_started_attempt_id = context.attempt_id or (
                f"direct-attempt-{context.attempt_number}"
            )
            task_started_attempt_number = context.attempt_number

        attempt_baseline = (
            task_baseline
            if not previous_baselines
            else self._capture_repository_baseline()
        )
        if not previous_baselines:
            retry_workspace = "initial_attempt"
        elif _baselines_have_same_candidate_state(
            attempt_baseline,
            task_baseline,
        ):
            retry_workspace = "restored_to_task_baseline"
        else:
            retry_workspace = "retained_cumulative_workspace"
        baselines = TaskRepositoryBaselines(
            task=task_baseline,
            attempt=attempt_baseline,
            task_started_attempt_id=task_started_attempt_id,
            task_started_attempt_number=task_started_attempt_number,
            attempt_id=context.attempt_id or f"direct-attempt-{context.attempt_number}",
            attempt_number=context.attempt_number,
            retry_workspace=retry_workspace,
        )
        updates: dict[str, Any] = {
            "repository_baselines": baselines.model_dump(mode="json")
        }
        if context.previous_attempts:
            feedback = self._build_retry_feedback(context, baselines)
            updates["retry_feedback"] = feedback.model_dump(mode="json")
        return updates

    def execute(self, context: TaskExecutionContext) -> TaskExecutionResult:
        context = self._context_with_repository_baselines(context)
        if self.runtime_trace is None:
            return self.prepare_retry_result(context, self._execute(context))
        span = self.runtime_trace.start_span(
            TraceSpanType.TASK,
            context.task.title,
            task_id=context.task.task_id,
            worker_id=self.worker_id,
            attributes={
                "attempt_number": context.attempt_number,
                "assigned_role": context.task.assigned_role,
                "risk": context.task.risk.value,
                "expected_output_count": len(context.task.expected_outputs),
                "done_criteria_count": len(context.task.done_criteria),
            },
        )
        try:
            with self.runtime_trace.scope(span):
                result = self.prepare_retry_result(context, self._execute(context))
        except BaseException as exc:
            self.runtime_trace.fail_span(
                span,
                exc,
                retryable=True,
                attributes={"attempt_number": context.attempt_number},
            )
            raise

        output = self.runtime_trace.capture_json_output(
            span,
            "task.execution-result",
            result.model_dump(mode="json"),
        )
        artifacts = [
            TraceArtifactReference(
                artifact_id=artifact.artifact_id,
                artifact_type=artifact.artifact_type,
                identifier=artifact.identifier,
            )
            for artifact in result.artifacts
        ]
        pending_approval = _pending_tool_approval(result)
        if result.succeeded:
            self.runtime_trace.finish_span(
                span,
                output_references=[output] if output is not None else [],
                artifact_references=artifacts,
                attributes={
                    "attempt_number": context.attempt_number,
                    "changed_file_count": len(result.changed_files),
                },
            )
        elif pending_approval is not None:
            self.runtime_trace.finish_span(
                span,
                output_references=[output] if output is not None else [],
                artifact_references=artifacts,
                attributes={
                    "attempt_id": context.attempt_id,
                    "attempt_number": context.attempt_number,
                    "changed_file_count": len(result.changed_files),
                    "suspended_for_tool_approval": True,
                    "approval_id": pending_approval.get("approval_id"),
                    "invocation_id": pending_approval.get("invocation_id"),
                    "tool_name": pending_approval.get("tool_name"),
                },
            )
        else:
            category = (
                result.failure_category.value
                if result.failure_category is not None
                else "unknown"
            )
            status = (
                TraceStatus.CANCELLED
                if result.failure_category == FailureCategory.CANCELLED
                else TraceStatus.FAILED
            )
            self.runtime_trace.finish_span(
                span,
                status=status,
                failure=(
                    None
                    if status == TraceStatus.CANCELLED
                    else TraceFailure(
                        category=category,
                        message=result.error_message or result.summary,
                        retryable=bool(result.retryable),
                    )
                ),
                output_references=[output] if output is not None else [],
                artifact_references=artifacts,
                attributes={
                    "attempt_number": context.attempt_number,
                    "changed_file_count": len(result.changed_files),
                },
            )
        return result

    def prepare_retry_result(
        self,
        context: TaskExecutionContext,
        result: TaskExecutionResult,
    ) -> TaskExecutionResult:
        """Attach immutable candidate-bound evidence before a retry is scheduled."""
        category = result.failure_category
        if (
            result.succeeded
            or category not in _RETRYABLE_FEEDBACK_CATEGORIES
            or result.retryable is False
            or _pending_tool_approval(result) is not None
        ):
            return result
        existing = result.metadata.get("retry_evidence")
        if isinstance(existing, dict):
            evidence = RetryEvidence.model_validate(existing)
            self._validate_retry_evidence_source(context, result, evidence)
            return result
        evidence = self._build_retry_evidence(context, result)
        return result.model_copy(
            update={
                "metadata": {
                    **result.metadata,
                    "retry_evidence": evidence.model_dump(mode="json"),
                }
            }
        )

    def _execute(self, context: TaskExecutionContext) -> TaskExecutionResult:
        _drain_model_results(self.coder)
        _drain_model_results(self.reviewer)
        verifier_continuation = self._verifier_continuation(context)
        plan = self._task_plan(context)
        coder_intelligence = None
        if self.intelligence_context is not None:
            coder_intelligence = build_coder_intelligence(
                self.intelligence_context,
                plan,
                task_id=context.task.task_id,
            ).render()
        retry_feedback = self._retry_feedback(context)
        reviewer_feedback = (
            None
            if retry_feedback is not None
            else self._previous_reviewer_feedback(context)
        )
        before = self._snapshot()
        coder_summary = (
            verifier_continuation.coder_summary
            if verifier_continuation is not None
            else ""
        )
        verifier_result: dict[str, Any] | None = None
        reviewer_result: dict[str, Any] | None = None
        verification_evidence: dict[str, Any] | None = None
        review_evidence: dict[str, Any] | None = None
        artifacts: list[ExecutionArtifact] = []
        analysis_artifact: ExecutionArtifact | None = None
        try:
            self._recover_tool_runtime(context.run.run_id)
            with model_request_context(
                run_id=context.run.run_id,
                task_id=context.task.task_id,
                cancellation=self.cancellation,
            ):
                if verifier_continuation is None:
                    self._checkpoint("before-coder")
                    coder_arguments = {
                        "user_task": context.run.original_task,
                        "plan": plan,
                        "reviewer_feedback": reviewer_feedback,
                        "retry_feedback": retry_feedback,
                        "cancellation": self.cancellation,
                        "tool_runtime": self.tool_runtime,
                        "run_id": context.run.run_id,
                        "task_id": context.task.task_id,
                        "workspace_trusted": True,
                        "provider_consented": True,
                        "policy_context": {
                            # Retry ordinals must not change an approved invocation's
                            # authorization identity during durable resume.
                            "assigned_role": context.task.assigned_role,
                            "planned_capabilities": list(
                                context.task.metadata.get(
                                    "required_capabilities",
                                    [],
                                )
                            ),
                        },
                        "repository_intelligence": coder_intelligence,
                        "attempt_id": context.attempt_id,
                        "attempt_number": context.attempt_number,
                        "loop_continuation": context.continuation,
                    }
                    coder_summary = self._trace_call(
                        TraceSpanType.CUSTOM,
                        "coder",
                        lambda: self.coder.execute(
                            **_supported_arguments(
                                self.coder.execute,
                                coder_arguments,
                            )
                        ),
                        capture="text",
                    )
                    self._checkpoint("after-coder")
                else:
                    self._validate_verifier_continuation(
                        context,
                        verifier_continuation,
                    )
                if context.task.execution_kind == TaskExecutionKind.ANALYSIS:
                    verifier_result = self._analysis_verifier_result()
                else:
                    verifier_result = self._trace_call(
                        TraceSpanType.VERIFIER,
                        "task verifier",
                        lambda: self.verifier.verify(
                            **_supported_arguments(
                                self.verifier.verify,
                                {
                                    "tool_runtime": self.tool_runtime,
                                    "run_id": context.run.run_id,
                                    "task_id": context.task.task_id,
                                    "invocation_key": (
                                        verifier_continuation.verifier_invocation_key
                                        if verifier_continuation is not None
                                        else f"attempt-{context.attempt_number}"
                                    ),
                                    "workspace_trusted": True,
                                    "provider_consented": True,
                                    "expected_command_sha256": (
                                        verifier_continuation.command_sha256
                                        if verifier_continuation is not None
                                        else None
                                    ),
                                },
                            )
                        ),
                        capture="json",
                    )
                self._checkpoint("after-verifier")
                verifier_status = str(
                    verifier_result.get("status")
                    or ("passed" if verifier_result.get("passed") else "failed")
                )
                if verifier_status == "awaiting_tool_approval":
                    return self._verifier_approval_pending_result(
                        context,
                        before,
                        coder_summary=coder_summary,
                        verifier_result=verifier_result,
                    )
                if verifier_status == "in_progress":
                    raise ManagedToolContinuationError(
                        "Verifier invocation is already in progress and cannot be "
                        "continued without an execution fence."
                    )
                changed_files, changes, artifacts = self._execution_artifacts(
                    context,
                    before,
                )
                if context.task.execution_kind == TaskExecutionKind.ANALYSIS:
                    if changed_files:
                        return self._analysis_mutation_result(
                            context,
                            coder_summary=coder_summary,
                            changed_files=changed_files,
                            changes=changes,
                            artifacts=artifacts,
                        )
                    analysis_artifact = self._analysis_artifact(
                        context,
                        coder_summary,
                    )
                    artifacts.append(analysis_artifact)
                    coder_summary = str(analysis_artifact.metadata["summary"])
                if not verifier_result.get("passed"):
                    return self._verifier_failure_result(
                        context,
                        coder_summary=coder_summary,
                        verifier_result=verifier_result,
                        verifier_status=verifier_status,
                        changed_files=changed_files,
                        changes=changes,
                        artifacts=artifacts,
                    )
                candidate = self._review_candidate(context, changes)
                verification_evidence = self._verification_evidence(
                    context,
                    verifier_result,
                    candidate,
                )
                task_diff = self._task_diff(
                    context,
                    changes,
                    candidate=candidate,
                )
                if not self._candidate_is_current(context, changes, candidate):
                    return self._source_identity_mismatch_result(
                        context,
                        before,
                        changed_files=changed_files,
                        changes=changes,
                        artifacts=artifacts,
                        stage="before_task_review",
                    )
                review_evidence = self._task_review_evidence(
                    context,
                    changes,
                    candidate,
                    verification_evidence,
                )
                reviewer_intelligence = None
                if self.intelligence_context is not None:
                    reviewer_intelligence = build_reviewer_intelligence(
                        self.intelligence_context,
                        plan,
                        changes.review_files,
                        task_id=context.task.task_id,
                    ).render()
                self._checkpoint("before-task-review")
                reviewer_result = self._trace_call(
                    TraceSpanType.REVIEWER,
                    "task reviewer",
                    lambda: self._review_task(
                        context,
                        plan,
                        changes,
                        task_diff,
                        coder_summary,
                        verifier_result,
                        reviewer_intelligence,
                        review_evidence,
                        artifact_identifiers=(
                            [analysis_artifact.identifier]
                            if analysis_artifact is not None
                            else None
                        ),
                    ),
                    capture="json",
                )
                self._checkpoint("after-task-review")
                if not self._candidate_is_current(context, changes, candidate):
                    return self._source_identity_mismatch_result(
                        context,
                        before,
                        changed_files=changed_files,
                        changes=changes,
                        artifacts=artifacts,
                        stage="after_task_review",
                    )
        except PlannedCapabilityMismatchError as exc:
            return self._plan_capability_mismatch_result(
                context,
                before,
                exc,
                coder_summary=coder_summary,
            )
        except StepBudgetExhaustedError as exc:
            return self._step_budget_exhausted_result(
                context,
                before,
                exc,
                coder_summary=coder_summary,
            )
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
        metadata = {
            "task_review": {
                "approved": bool(reviewer_result.get("approved")),
                "issues": reviewer_result.get("issues", []),
                "summary": reviewer_result.get("summary", ""),
                "required_fixes": reviewer_result.get("required_fixes", []),
                "unplanned_affected_components": reviewer_result.get(
                    "unplanned_affected_components",
                    [],
                ),
                "missing_tests": reviewer_result.get("missing_tests", []),
                "boundary_violations": reviewer_result.get(
                    "boundary_violations",
                    [],
                ),
                "index_uncertainty": reviewer_result.get(
                    "index_uncertainty",
                    [],
                ),
            },
            "verifier": {
                "passed": bool(verifier_result.get("passed")),
                "command": verifier_result.get("command", []),
                "exit_code": verifier_result.get("exit_code"),
                "reason": verifier_result.get("reason"),
                "status": verifier_status,
                "skipped": bool(verifier_result.get("skipped")),
                "artifact_suppression_active": bool(
                    verifier_result.get("artifact_suppression_active")
                ),
                "pytest_cache_disabled": bool(
                    verifier_result.get("pytest_cache_disabled")
                ),
            },
            **self._execution_metadata(context, changes),
            "verification_evidence": verification_evidence,
            "review_evidence": review_evidence,
            "task_contract": {
                "execution_kind": context.task.execution_kind.value,
                "required_capabilities": _bounded_capability_names(
                    context.task.metadata.get("required_capabilities", [])
                ),
            },
            "analysis": (
                {
                    "artifact_identifier": analysis_artifact.identifier,
                    "summary_chars": analysis_artifact.metadata["summary_chars"],
                    "truncated": analysis_artifact.metadata["truncated"],
                    "repository_mutation_observed": False,
                }
                if analysis_artifact is not None
                else None
            ),
            "repository_intelligence": {
                "context_hash": (
                    self.intelligence_context.context_hash
                    if self.intelligence_context is not None
                    else None
                ),
                "coder_guidance_used": coder_intelligence is not None,
                "reviewer_guidance_used": reviewer_intelligence is not None,
            },
            # Retained for retry feedback compatibility with persisted attempts.
            "reviewer_feedback": {
                "approved": bool(reviewer_result.get("approved")),
                "issues": reviewer_result.get("issues", []),
                "summary": reviewer_result.get("summary", ""),
                "required_fixes": reviewer_result.get("required_fixes", []),
                "unplanned_affected_components": reviewer_result.get(
                    "unplanned_affected_components",
                    [],
                ),
                "missing_tests": reviewer_result.get("missing_tests", []),
                "boundary_violations": reviewer_result.get(
                    "boundary_violations",
                    [],
                ),
                "index_uncertainty": reviewer_result.get(
                    "index_uncertainty",
                    [],
                ),
            },
            "model_requests": [
                *(_drain_model_results(self.coder)),
                *(_drain_model_results(self.reviewer)),
            ],
        }
        if not reviewer_result.get("approved"):
            return TaskExecutionResult(
                succeeded=False,
                summary=reviewer_result.get("summary", "Reviewer rejected the task."),
                failure_category=FailureCategory.REVIEWER_REJECTION,
                error_message="The reviewer requested corrections.",
                retryable=True,
                artifacts=artifacts,
                verifier_status=verifier_status,
                reviewer_status="rejected",
                changed_files=changed_files,
                metadata=metadata,
            )

        return TaskExecutionResult(
            succeeded=True,
            summary=coder_summary,
            artifacts=artifacts,
            verifier_status=verifier_status,
            reviewer_status="approved",
            changed_files=changed_files,
            metadata=metadata,
        )

    @staticmethod
    def _analysis_verifier_result() -> dict[str, Any]:
        return {
            "command": [],
            "exit_code": None,
            "passed": True,
            "output": None,
            "reason": (
                "Code verification is not applicable to an explicit analysis task."
            ),
            "status": "not_applicable",
            "skipped": True,
        }

    @staticmethod
    def _analysis_artifact(
        context: TaskExecutionContext,
        coder_summary: str,
    ) -> ExecutionArtifact:
        raw_summary = str(coder_summary or "")
        bounded_summary = raw_summary[:_ANALYSIS_SUMMARY_MAX_CHARS]
        digest = hashlib.sha256(bounded_summary.encode("utf-8")).hexdigest()
        return ExecutionArtifact(
            artifact_id=uuid.uuid4().hex,
            run_id=context.run.run_id,
            task_id=context.task.task_id,
            artifact_type="analysis_summary",
            identifier=f"analysis:{digest}",
            metadata={
                "attempt_number": context.attempt_number,
                "summary": bounded_summary,
                "summary_chars": len(bounded_summary),
                "source_summary_chars": len(raw_summary),
                "truncated": len(raw_summary) > len(bounded_summary),
                "review_eligible": True,
                "commit_eligible": False,
            },
        )

    def _analysis_mutation_result(
        self,
        context: TaskExecutionContext,
        *,
        coder_summary: str,
        changed_files: list[str],
        changes: RepositoryChangeSet,
        artifacts: list[ExecutionArtifact],
    ) -> TaskExecutionResult:
        return TaskExecutionResult(
            succeeded=False,
            summary="Analysis task violated its read-only repository contract.",
            artifacts=artifacts,
            failure_category=FailureCategory.POLICY_VIOLATION,
            error_message=(
                "Repository changes were observed during an explicit analysis task."
            ),
            retryable=False,
            verifier_status="not_applicable",
            reviewer_status="not_run",
            changed_files=changed_files,
            metadata={
                **self._execution_metadata(context, changes),
                "coder_summary": coder_summary[:_ANALYSIS_SUMMARY_MAX_CHARS],
                "task_contract": {
                    "execution_kind": context.task.execution_kind.value,
                    "required_capabilities": _bounded_capability_names(
                        context.task.metadata.get("required_capabilities", [])
                    ),
                },
                "analysis": {
                    "artifact_identifier": None,
                    "repository_mutation_observed": True,
                    "changed_file_count": len(changed_files),
                },
                "model_requests": [
                    *(_drain_model_results(self.coder)),
                    *(_drain_model_results(self.reviewer)),
                ],
            },
        )

    def _plan_capability_mismatch_result(
        self,
        context: TaskExecutionContext,
        before: dict[str, str],
        mismatch: PlannedCapabilityMismatchError,
        *,
        coder_summary: str,
    ) -> TaskExecutionResult:
        changed_files, changes, artifacts = self._execution_artifacts(
            context,
            before,
        )
        mismatch_metadata = mismatch.safe_metadata()
        return TaskExecutionResult(
            succeeded=False,
            summary="Planner capability contract prevented task completion.",
            artifacts=artifacts,
            failure_category=FailureCategory.PLAN_CAPABILITY_MISMATCH,
            error_message=str(mismatch),
            retryable=False,
            verifier_status="not_run",
            reviewer_status="not_run",
            changed_files=changed_files,
            metadata={
                **self._execution_metadata(context, changes),
                "coder_summary": coder_summary,
                "plan_capability_mismatch": mismatch_metadata,
                "task_contract": {
                    "execution_kind": context.task.execution_kind.value,
                    "required_capabilities": mismatch.declared_capabilities,
                },
                "model_requests": [
                    *(_drain_model_results(self.coder)),
                    *(_drain_model_results(self.reviewer)),
                ],
            },
        )

    def _step_budget_exhausted_result(
        self,
        context: TaskExecutionContext,
        before: dict[str, str],
        error: StepBudgetExhaustedError,
        *,
        coder_summary: str,
    ) -> TaskExecutionResult:
        changed_files, changes, artifacts = self._execution_artifacts(
            context,
            before,
        )
        return TaskExecutionResult(
            succeeded=False,
            summary="Coder action budget was exhausted after consuming a final observation.",
            artifacts=artifacts,
            failure_category=FailureCategory.STEP_BUDGET_EXHAUSTED,
            error_message=str(error),
            retryable=False,
            verifier_status="not_run",
            reviewer_status="not_run",
            changed_files=changed_files,
            metadata={
                **self._execution_metadata(context, changes),
                "coder_summary": coder_summary,
                "step_budget": error.safe_metadata(),
                "model_requests": [
                    *(_drain_model_results(self.coder)),
                    *(_drain_model_results(self.reviewer)),
                ],
            },
        )

    def _execution_artifacts(
        self,
        context: TaskExecutionContext,
        before: dict[str, str],
    ) -> tuple[list[str], RepositoryChangeSet, list[ExecutionArtifact]]:
        changed_files = self._changed_since(self._task_snapshot(context))
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
        return changed_files, changes, artifacts

    @staticmethod
    def _verifier_continuation(
        context: TaskExecutionContext,
    ) -> VerifierContinuation | None:
        raw = context.continuation
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ManagedToolContinuationError(
                "Persisted task continuation is not an object."
            )
        stage = raw.get("stage")
        if stage is None:
            return None
        if stage != "verifier":
            raise ManagedToolContinuationError(
                "Persisted task continuation has an unsupported stage."
            )
        try:
            return VerifierContinuation.model_validate(raw)
        except ValidationError as exc:
            raise ManagedToolContinuationError(
                "Persisted verifier continuation is malformed or incompatible."
            ) from exc

    def _validate_verifier_continuation(
        self,
        context: TaskExecutionContext,
        continuation: VerifierContinuation,
    ) -> None:
        if (
            continuation.run_id != context.run.run_id
            or continuation.task_id != context.task.task_id
            or continuation.attempt_id != context.attempt_id
            or continuation.attempt_number != context.attempt_number
        ):
            raise ManagedToolContinuationError(
                "Verifier continuation run, task, or attempt identity does not match."
            )
        task_sha256 = hashlib.sha256(
            context.run.original_task.encode("utf-8")
        ).hexdigest()
        if task_sha256 != continuation.user_task_sha256:
            raise ManagedToolContinuationError(
                "Verifier continuation task content does not match."
            )
        expected_key = f"attempt-{context.attempt_number}"
        if continuation.verifier_invocation_key != expected_key:
            raise ManagedToolContinuationError(
                "Verifier continuation invocation key does not match its attempt."
            )
        if self._review_source_snapshot() != continuation.source_snapshot:
            raise ManagedToolContinuationError(
                "Review-eligible source changed while verifier approval was suspended."
            )
        validate_exact_tool_approval(
            self.tool_runtime,
            continuation,
            idempotency_key=f"verifier:{continuation.verifier_invocation_key}",
            caller_role="verifier",
        )

    def _verifier_approval_pending_result(
        self,
        context: TaskExecutionContext,
        before: dict[str, str],
        *,
        coder_summary: str,
        verifier_result: dict[str, Any],
    ) -> TaskExecutionResult:
        raw_approval = verifier_result.get("tool_approval")
        if not isinstance(raw_approval, dict):
            raise ManagedToolContinuationError(
                "Verifier approval suspension omitted its exact approval identity."
            )
        safe_summary = sanitize_json(coder_summary, max_chars=20_000)
        if not isinstance(safe_summary, str):
            raise ManagedToolContinuationError(
                "Coder summary could not be persisted safely for verifier continuation."
            )
        attempt_snapshot = self._attempt_snapshot(context, before)
        changed_files, changes, artifacts = self._execution_artifacts(context, before)
        payload = {
            "run_id": context.run.run_id,
            "task_id": context.task.task_id,
            "attempt_id": context.attempt_id,
            "attempt_number": context.attempt_number,
            "user_task_sha256": hashlib.sha256(
                context.run.original_task.encode("utf-8")
            ).hexdigest(),
            "verifier_invocation_key": f"attempt-{context.attempt_number}",
            "coder_summary": safe_summary[:20_000],
            "worktree_snapshot": attempt_snapshot,
            "source_snapshot": self._review_source_snapshot(),
            "repository_baselines": self._repository_baselines(context).model_dump(
                mode="json"
            ),
            "command_sha256": verifier_result.get("command_sha256"),
            **raw_approval,
        }
        try:
            continuation = VerifierContinuation.model_validate(payload)
        except ValidationError as exc:
            raise ManagedToolContinuationError(
                "Verifier approval state could not be persisted as a bounded continuation."
            ) from exc
        pending = {
            "approval_id": continuation.approval_id,
            "invocation_id": continuation.invocation_id,
            "tool_name": continuation.tool_name,
        }
        return TaskExecutionResult(
            succeeded=False,
            summary="Verification is awaiting exact tool approval.",
            artifacts=artifacts,
            failure_category=None,
            error_message=None,
            retryable=False,
            verifier_status="awaiting_tool_approval",
            reviewer_status="not_run",
            changed_files=changed_files,
            metadata={
                **self._execution_metadata(context, changes),
                "coder_summary": safe_summary[:20_000],
                "tool_approval": pending,
                "verifier": {
                    "passed": False,
                    "status": "awaiting_tool_approval",
                    "command_sha256": continuation.command_sha256,
                },
                "_agentbus": {
                    "tool_approval_pending": pending,
                    "task_continuation": continuation.model_dump(mode="json"),
                },
                "model_requests": _drain_model_results(self.coder),
            },
        )

    def _verifier_failure_result(
        self,
        context: TaskExecutionContext,
        *,
        coder_summary: str,
        verifier_result: dict[str, Any],
        verifier_status: str,
        changed_files: list[str],
        changes: RepositoryChangeSet,
        artifacts: list[ExecutionArtifact],
    ) -> TaskExecutionResult:
        verifier_failure = _bounded_verifier_failure(
            verifier_result,
            verifier_status,
        )
        return TaskExecutionResult(
            succeeded=False,
            summary=f"Verification failed after coder output: {coder_summary}",
            failure_category=FailureCategory.VERIFIER_FAILURE,
            error_message="The verifier command did not pass.",
            retryable=True,
            artifacts=artifacts,
            verifier_status=verifier_status,
            reviewer_status="not_run",
            changed_files=changed_files,
            metadata={
                **self._execution_metadata(context, changes),
                "coder_summary": coder_summary[:20_000],
                "verifier": verifier_failure,
                "model_requests": _drain_model_results(self.coder),
            },
        )

    def _verification_evidence(
        self,
        context: TaskExecutionContext,
        verifier_result: dict[str, Any],
        candidate: dict[str, Any],
    ) -> dict[str, Any] | None:
        invocation_id = verifier_result.get("tool_invocation_id")
        command_sha256 = verifier_result.get("command_sha256")
        if not verifier_result.get("passed"):
            return None
        baselines = self._repository_baselines(context)
        return {
            "status": "passed",
            "task_id": context.task.task_id,
            "attempt_id": context.attempt_id,
            "attempt_number": context.attempt_number,
            "invocation_key": f"attempt-{context.attempt_number}",
            "invocation_id": invocation_id,
            "invocation_revision": verifier_result.get("tool_invocation_revision", 1),
            "command_sha256": command_sha256,
            "source_snapshot": self._review_source_snapshot(),
            "task_baseline_identity_sha256": baselines.task.identity_sha256,
            "attempt_baseline_identity_sha256": baselines.attempt.identity_sha256,
            "candidate_identity_sha256": candidate.get("identity_sha256"),
            "candidate_tree_id": candidate.get("tree_id"),
        }

    def _approval_pending_result(
        self,
        context: TaskExecutionContext,
        before: dict[str, str],
        approval: ManagedToolApprovalRequired,
        *,
        coder_summary: str,
    ) -> TaskExecutionResult:
        attempt_snapshot = self._attempt_snapshot(context, before)
        changed_files, changes, artifacts = self._execution_artifacts(context, before)
        pending = {
            "approval_id": approval.approval_id,
            "invocation_id": approval.invocation_id,
            "tool_name": approval.tool_name,
        }
        if approval.continuation is None:
            raise RuntimeError(
                "Tool approval pause omitted its bounded loop continuation."
            )
        continuation = approval.continuation.model_validate(
            approval.continuation.model_dump(mode="json")
            | {
                "worktree_snapshot": attempt_snapshot,
                "repository_baselines": self._repository_baselines(
                    context
                ).model_dump(mode="json"),
            }
        )
        return TaskExecutionResult(
            succeeded=False,
            summary=str(approval),
            artifacts=artifacts,
            failure_category=None,
            error_message=None,
            retryable=False,
            verifier_status="awaiting_tool_approval",
            changed_files=changed_files,
            metadata={
                **self._execution_metadata(context, changes),
                "coder_summary": coder_summary,
                "tool_approval": pending,
                "_agentbus": {
                    "tool_approval_pending": pending,
                    "loop_continuation": continuation.model_dump(mode="json"),
                },
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
        changed_files, changes, artifacts = self._execution_artifacts(context, before)
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
                **self._execution_metadata(context, changes),
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

    def _trace_call(
        self,
        span_type: TraceSpanType,
        name: str,
        function,
        *,
        capture: str,
    ):
        if self.runtime_trace is None:
            return function()
        return self.runtime_trace.call(
            span_type,
            name,
            function,
            worker_id=self.worker_id,
            capture=capture,
        )

    def _task_plan(self, context: TaskExecutionContext) -> dict[str, Any]:
        task = context.task
        step = {
            "id": task.task_id,
            "title": task.title,
            "description": task.description,
            "risk": task.risk.value,
            "execution_kind": task.execution_kind.value,
            "dependencies": task.dependency_ids,
            "assigned_role": task.assigned_role,
            "expected_outputs": task.expected_outputs,
            "done_criteria": task.done_criteria,
            "required_capabilities": list(
                task.metadata.get("required_capabilities", [])
            ),
        }
        for field_name in (
            "targeted_files",
            "targeted_symbols",
            "expected_impacted_components",
            "proposed_tests",
            "architecture_constraints",
        ):
            if field_name in task.metadata:
                step[field_name] = list(task.metadata[field_name])
        plan = {
            "goal": context.run.planner_output.get("goal", context.run.original_task),
            "steps": [step],
            "test_strategy": context.run.planner_output.get(
                "test_strategy", "Run the detected test command."
            ),
            "done_criteria": task.done_criteria,
        }
        for field_name in (
            "intelligence_snapshot_id",
            "intelligence_context_hash",
            "intelligence_warnings",
            "intelligence_scope_validated",
        ):
            if field_name in context.run.planner_output:
                plan[field_name] = context.run.planner_output[field_name]
        return plan

    @staticmethod
    def _previous_reviewer_feedback(
        context: TaskExecutionContext,
    ) -> dict[str, Any] | None:
        if not context.previous_attempts:
            return None
        feedback = context.previous_attempts[-1].metadata.get("reviewer_feedback")
        return feedback if isinstance(feedback, dict) else None

    def _build_retry_evidence(
        self,
        context: TaskExecutionContext,
        result: TaskExecutionResult,
    ) -> RetryEvidence:
        category = result.failure_category
        if category not in _RETRYABLE_FEEDBACK_CATEGORIES:
            raise RetryEvidenceSourceMismatch(
                "Failure category does not support corrective retry evidence."
            )
        candidate, changes = self._candidate_for_task_baseline(
            self._repository_baselines(context).task
        )
        diagnostics = self._retry_diagnostics(result)
        payload = {
            "schema_version": 1,
            "source_attempt_id": context.attempt_id
            or f"direct-attempt-{context.attempt_number}",
            "source_attempt_number": context.attempt_number,
            "failure_category": category.value,
            "candidate_identity_sha256": str(candidate["identity_sha256"]),
            "candidate_tree_id": candidate.get("tree_id"),
            "candidate_source_sha256": sha256_json(
                candidate.get("source_snapshot", {})
            ),
            "retained_changed_files": changes.changed_files,
            "diagnostics": diagnostics.model_dump(mode="json"),
            "diagnostics_sha256": sha256_json(
                diagnostics.model_dump(mode="json")
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return RetryEvidence(
            **payload,
            evidence_sha256=sha256_json(payload),
        )

    def _validate_retry_evidence_source(
        self,
        context: TaskExecutionContext,
        result: TaskExecutionResult,
        evidence: RetryEvidence,
    ) -> None:
        if (
            evidence.source_attempt_id
            != (context.attempt_id or f"direct-attempt-{context.attempt_number}")
            or evidence.source_attempt_number != context.attempt_number
            or result.failure_category is None
            or evidence.failure_category != result.failure_category.value
        ):
            raise RetryEvidenceSourceMismatch(
                "Persisted retry evidence does not match its source attempt."
            )
        candidate, changes = self._candidate_for_task_baseline(
            self._repository_baselines(context).task
        )
        current = (
            candidate.get("identity_sha256"),
            candidate.get("tree_id"),
            sha256_json(candidate.get("source_snapshot", {})),
            changes.changed_files,
        )
        expected = (
            evidence.candidate_identity_sha256,
            evidence.candidate_tree_id,
            evidence.candidate_source_sha256,
            evidence.retained_changed_files,
        )
        if current != expected:
            raise RetryEvidenceSourceMismatch(
                "Persisted retry evidence no longer matches its failed candidate."
            )

    def _build_retry_feedback(
        self,
        context: TaskExecutionContext,
        baselines: TaskRepositoryBaselines,
    ) -> RetryFeedback:
        previous = sorted(
            context.previous_attempts,
            key=lambda item: item.attempt_number,
        )[-1]
        raw_evidence = previous.metadata.get("retry_evidence")
        if not isinstance(raw_evidence, dict):
            raise RetryEvidenceSourceMismatch(
                "The previous attempt omitted persisted corrective retry evidence."
            )
        try:
            evidence = RetryEvidence.model_validate(raw_evidence)
        except ValidationError as exc:
            raise RetryEvidenceSourceMismatch(
                "The previous attempt contains malformed corrective retry evidence."
            ) from exc
        if (
            evidence.source_attempt_id != previous.attempt_id
            or evidence.source_attempt_number != previous.attempt_number
            or previous.error_category is None
            or evidence.failure_category != previous.error_category.value
        ):
            raise RetryEvidenceSourceMismatch(
                "Corrective retry evidence does not match the previous attempt."
            )

        candidate, changes = self._candidate_for_task_baseline(baselines.task)
        active_identity = str(candidate["identity_sha256"])
        active_tree = candidate.get("tree_id")
        active_source = sha256_json(candidate.get("source_snapshot", {}))
        retained = (
            active_identity == evidence.candidate_identity_sha256
            and active_tree == evidence.candidate_tree_id
            and active_source == evidence.candidate_source_sha256
            and changes.changed_files == evidence.retained_changed_files
        )
        if retained:
            disposition = "retained_candidate"
        elif baselines.retry_workspace == "restored_to_task_baseline":
            disposition = "restored_to_task_baseline"
        else:
            raise RetryEvidenceSourceMismatch(
                "Retry evidence source drifted before the destination attempt started."
            )
        payload = {
            "schema_version": 1,
            "source_evidence": evidence.model_dump(mode="json"),
            "destination_attempt_id": context.attempt_id
            or f"direct-attempt-{context.attempt_number}",
            "destination_attempt_number": context.attempt_number,
            "source_disposition": disposition,
            "active_candidate_identity_sha256": active_identity,
            "active_candidate_tree_id": active_tree,
            "active_candidate_source_sha256": active_source,
            "active_changed_files": changes.changed_files,
            "mutations_retained": retained,
        }
        return RetryFeedback(
            **payload,
            context_sha256=sha256_json(payload),
        )

    def _validate_retry_feedback(
        self,
        context: TaskExecutionContext,
        raw: dict[str, Any],
    ) -> RetryFeedback:
        try:
            feedback = RetryFeedback.model_validate(raw)
        except ValidationError as exc:
            raise RetryEvidenceSourceMismatch(
                "Persisted destination retry feedback is malformed."
            ) from exc
        destination_id = context.attempt_id or (
            f"direct-attempt-{context.attempt_number}"
        )
        if (
            feedback.destination_attempt_id != destination_id
            or feedback.destination_attempt_number != context.attempt_number
        ):
            raise RetryEvidenceSourceMismatch(
                "Persisted retry feedback does not match its destination attempt."
            )
        if context.previous_attempts:
            previous = sorted(
                context.previous_attempts,
                key=lambda item: item.attempt_number,
            )[-1]
            raw_source = previous.metadata.get("retry_evidence")
            if (
                not isinstance(raw_source, dict)
                or feedback.source_evidence.model_dump(mode="json") != raw_source
            ):
                raise RetryEvidenceSourceMismatch(
                    "Persisted retry feedback no longer matches its source record."
                )
        return feedback

    def _retry_feedback(
        self,
        context: TaskExecutionContext,
    ) -> dict[str, Any] | None:
        if not context.previous_attempts:
            return None
        raw = context.attempt_metadata.get("retry_feedback")
        if not isinstance(raw, dict):
            if context.continuation is not None:
                # Preserve exact pre-upgrade approval continuations without changing
                # the task text whose hash was already checkpointed.
                return None
            raise RetryEvidenceSourceMismatch(
                "The retry attempt omitted its checkpointed corrective feedback."
            )
        feedback = self._validate_retry_feedback(context, raw)
        if context.continuation is None:
            baselines = self._repository_baselines(context)
            candidate, changes = self._candidate_for_task_baseline(baselines.task)
            current = (
                candidate.get("identity_sha256"),
                candidate.get("tree_id"),
                sha256_json(candidate.get("source_snapshot", {})),
                changes.changed_files,
            )
            expected = (
                feedback.active_candidate_identity_sha256,
                feedback.active_candidate_tree_id,
                feedback.active_candidate_source_sha256,
                feedback.active_changed_files,
            )
            if current != expected:
                raise RetryEvidenceSourceMismatch(
                    "Retry evidence source changed after feedback was checkpointed."
                )
        return feedback.model_dump(mode="json")

    def _candidate_for_task_baseline(
        self,
        baseline: RepositoryBaseline,
    ) -> tuple[dict[str, Any], RepositoryChangeSet]:
        changed_files = self._changed_since(baseline.worktree_snapshot)
        changes = self._change_set(changed_files)
        capture = getattr(self.git_repository, "review_candidate", None)
        if capture is not None and baseline.tree_id is not None:
            candidate = capture(baseline.model_dump(mode="json"))
            if not isinstance(candidate, dict):
                raise RetryEvidenceSourceMismatch(
                    "Git repository returned invalid retry candidate evidence."
                )
        else:
            current_source = self._review_source_snapshot()
            source_snapshot = {
                path: current_source[path]
                for path in changes.review_files
                if path in current_source
            }
            payload = {
                "schema_version": 1,
                "head_commit": baseline.head_commit,
                "tree_id": None,
                "source_snapshot": source_snapshot,
                "changed_files": changes.review_files,
            }
            candidate = {**payload, "identity_sha256": sha256_json(payload)}
        if (
            sorted(candidate.get("changed_files", [])) != changes.review_files
            or not isinstance(candidate.get("identity_sha256"), str)
        ):
            raise RetryEvidenceSourceMismatch(
                "Retry candidate files disagree with the task baseline."
            )
        return candidate, changes

    @staticmethod
    def _retry_diagnostics(result: TaskExecutionResult) -> RetryDiagnostics:
        category = result.failure_category or FailureCategory.UNKNOWN
        if category == FailureCategory.VERIFIER_FAILURE:
            verifier = result.metadata.get("verifier", {})
            verifier = verifier if isinstance(verifier, dict) else {}
            return RetryDiagnostics(
                kind="verifier",
                summary=_bounded_diagnostic(
                    verifier.get("diagnostic_summary")
                    or result.error_message
                    or result.summary,
                    4_000,
                ),
                command=_bounded_command(verifier.get("command")),
                exit_status=_safe_exit_status(verifier.get("exit_code")),
                stdout=_bounded_diagnostic(verifier.get("stdout")),
                stderr=_bounded_diagnostic(verifier.get("stderr")),
                stdout_truncated=bool(verifier.get("stdout_truncated")),
                stderr_truncated=bool(verifier.get("stderr_truncated")),
                failing_tests=_bounded_diagnostic_items(
                    verifier.get("failing_tests", []),
                    max_items=12,
                    max_chars=512,
                ),
                exception_details=_bounded_diagnostic_items(
                    verifier.get("exception_details", []),
                    max_items=8,
                    max_chars=512,
                ),
            )
        if category == FailureCategory.REVIEWER_REJECTION:
            review = result.metadata.get("task_review")
            if not isinstance(review, dict):
                review = result.metadata.get("reviewer_feedback", {})
            review = review if isinstance(review, dict) else {}
            return RetryDiagnostics(
                kind="reviewer",
                summary=_bounded_diagnostic(
                    review.get("summary") or result.error_message or result.summary,
                    4_000,
                ),
                reviewer_issues=_bounded_diagnostic_items(
                    review.get("issues", []),
                    max_items=16,
                    max_chars=768,
                ),
                required_fixes=_bounded_diagnostic_items(
                    review.get("required_fixes", []),
                    max_items=16,
                    max_chars=768,
                ),
            )
        if category in {
            FailureCategory.MODEL_OUTPUT_ERROR,
            FailureCategory.MODEL_PROVIDER_ERROR,
            FailureCategory.MODEL_TRANSPORT_ERROR,
        }:
            provider = result.metadata.get("provider_failure", {})
            return RetryDiagnostics(
                kind="model_provider",
                summary=_bounded_diagnostic(
                    result.error_message or result.summary,
                    4_000,
                ),
                exception_details=_bounded_diagnostic_items(
                    [provider] if provider else []
                ),
            )
        kind = {
            FailureCategory.COMMAND_FAILURE: "command",
            FailureCategory.TOOL_VALIDATION_ERROR: "tool",
            FailureCategory.INTERRUPTED: "interrupted",
        }.get(category, "other")
        return RetryDiagnostics(
            kind=kind,
            summary=_bounded_diagnostic(
                result.error_message or result.summary,
                4_000,
            ),
            exception_details=_bounded_diagnostic_items(
                result.metadata.get("diagnostics", [])
                if isinstance(result.metadata, dict)
                else []
            ),
        )

    def _context_with_repository_baselines(
        self,
        context: TaskExecutionContext,
    ) -> TaskExecutionContext:
        raw = context.attempt_metadata.get("repository_baselines")
        if not isinstance(raw, dict):
            updates = self.prepare_attempt(context)
            raw = updates["repository_baselines"]
            context = context.model_copy(
                update={
                    "attempt_metadata": {
                        **context.attempt_metadata,
                        **updates,
                    }
                }
            )
        self._validate_repository_baselines(context, raw)
        return context

    def _validate_repository_baselines(
        self,
        context: TaskExecutionContext,
        raw: dict[str, Any],
    ) -> TaskRepositoryBaselines:
        try:
            baselines = TaskRepositoryBaselines.model_validate(raw)
        except ValidationError as exc:
            raise RepositoryBaselineMismatch(
                "Persisted durable task repository baselines are malformed."
            ) from exc
        if (
            baselines.attempt_number != context.attempt_number
            or (
                context.attempt_id is not None
                and baselines.attempt_id != context.attempt_id
            )
        ):
            raise RepositoryBaselineMismatch(
                "Persisted repository baseline does not match the active attempt."
            )
        continuation = context.continuation
        if isinstance(continuation, dict):
            continuation_baselines = continuation.get("repository_baselines")
            if (
                isinstance(continuation_baselines, dict)
                and continuation_baselines != baselines.model_dump(mode="json")
            ):
                raise RepositoryBaselineMismatch(
                    "Approval continuation repository baseline does not match the attempt."
                )
        return baselines

    def _repository_baselines(
        self,
        context: TaskExecutionContext,
    ) -> TaskRepositoryBaselines:
        raw = context.attempt_metadata.get("repository_baselines")
        if not isinstance(raw, dict):
            raise RepositoryBaselineMismatch(
                "Durable task execution omitted its checkpointed repository baselines."
            )
        return self._validate_repository_baselines(context, raw)

    def _capture_repository_baseline(self) -> RepositoryBaseline:
        capture = getattr(self.git_repository, "capture_review_baseline", None)
        if capture is not None:
            try:
                return RepositoryBaseline.model_validate(capture())
            except ValidationError as exc:
                raise RepositoryBaselineMismatch(
                    "Git repository returned an invalid immutable review baseline."
                ) from exc

        worktree_snapshot = self._snapshot()
        review_source_snapshot = self._review_source_snapshot()
        head = getattr(self.git_repository, "head_commit", None)
        raw_head_commit = head(short=False) if head is not None else None
        head_commit = _canonical_object_id(raw_head_commit)
        state = getattr(self.git_repository, "repository_state_sha256", None)
        state_sha256 = (
            state()
            if state is not None
            else sha256_json(
                {
                    "head_commit": head_commit,
                    "worktree_snapshot": worktree_snapshot,
                    "review_source_snapshot": review_source_snapshot,
                }
            )
        )
        payload = {
            "schema_version": 1,
            "head_commit": head_commit,
            "tree_id": None,
            "worktree_snapshot": worktree_snapshot,
            "review_source_snapshot": review_source_snapshot,
            "review_files": sorted(review_source_snapshot),
            "state_sha256": state_sha256,
        }
        return RepositoryBaseline.model_validate(
            {**payload, "identity_sha256": sha256_json(payload)}
        )

    def _changed_files(self) -> list[str]:
        if not self.git_repository.is_git_repo():
            return []
        return self.git_repository.changed_files()

    def _snapshot(self) -> dict[str, str]:
        snapshot = getattr(self.git_repository, "worktree_snapshot", None)
        if snapshot is None:
            return {}
        return snapshot()

    def _review_source_snapshot(self) -> dict[str, str]:
        snapshot = getattr(self.git_repository, "review_source_snapshot", None)
        if snapshot is not None:
            return snapshot()
        current = self._snapshot()
        review_files = set(self._change_set(self._changed_files()).review_files)
        return {
            path: identity
            for path, identity in current.items()
            if path in review_files
        }

    def _changed_since(self, before: dict[str, str]) -> list[str]:
        changed_since = getattr(self.git_repository, "changed_since", None)
        if changed_since is None:
            return self._changed_files()
        return changed_since(before)

    @staticmethod
    def _attempt_snapshot(
        context: TaskExecutionContext,
        current_snapshot: dict[str, str],
    ) -> dict[str, str]:
        raw_baselines = context.attempt_metadata.get("repository_baselines")
        if isinstance(raw_baselines, dict):
            try:
                return dict(
                    TaskRepositoryBaselines.model_validate(
                        raw_baselines
                    ).attempt.worktree_snapshot
                )
            except ValidationError as exc:
                raise RepositoryBaselineMismatch(
                    "Attempt repository baseline is malformed."
                ) from exc
        continuation = context.continuation
        if not isinstance(continuation, dict):
            return current_snapshot
        persisted = continuation.get("worktree_snapshot")
        if not isinstance(persisted, dict):
            return current_snapshot
        return {
            str(path): str(identity)
            for path, identity in persisted.items()
        }

    def _task_snapshot(self, context: TaskExecutionContext) -> dict[str, str]:
        return dict(self._repository_baselines(context).task.worktree_snapshot)

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

    def _execution_metadata(
        self,
        context: TaskExecutionContext,
        changes: RepositoryChangeSet,
    ) -> dict[str, Any]:
        baselines = self._repository_baselines(context)
        attempt_files = self._changed_since(baselines.attempt.worktree_snapshot)
        attempt_changes = self._change_set(attempt_files)
        return {
            "artifact_hygiene": changes.to_metadata(),
            "attempt_artifact_hygiene": attempt_changes.to_metadata(),
            "repository_baselines": baselines.model_dump(mode="json"),
            "repository_diff_scope": {
                "task_review": "cumulative_task",
                "attempt_diagnostics": "attempt_local",
                "retry_workspace": baselines.retry_workspace,
            },
        }

    def _review_candidate(
        self,
        context: TaskExecutionContext,
        changes: RepositoryChangeSet,
    ) -> dict[str, Any]:
        baselines = self._repository_baselines(context)
        capture = getattr(self.git_repository, "review_candidate", None)
        if capture is not None and baselines.task.tree_id is not None:
            candidate = capture(baselines.task.model_dump(mode="json"))
            if not isinstance(candidate, dict):
                raise RepositoryBaselineMismatch(
                    "Git repository returned an invalid review candidate."
                )
        else:
            current_source = self._review_source_snapshot()
            source_snapshot = {
                path: current_source[path]
                for path in changes.review_files
                if path in current_source
            }
            payload = {
                "schema_version": 1,
                "head_commit": baselines.task.head_commit,
                "tree_id": None,
                "source_snapshot": source_snapshot,
                "changed_files": changes.review_files,
            }
            candidate = {
                **payload,
                "identity_sha256": sha256_json(payload),
            }
        if sorted(candidate.get("changed_files", [])) != changes.review_files:
            raise RepositoryBaselineMismatch(
                "Reviewer candidate files disagree with cumulative task changes."
            )
        return candidate

    def _candidate_is_current(
        self,
        context: TaskExecutionContext,
        changes: RepositoryChangeSet,
        expected: dict[str, Any],
    ) -> bool:
        current = self._review_candidate(context, changes)
        return (
            current.get("identity_sha256") == expected.get("identity_sha256")
            and current.get("tree_id") == expected.get("tree_id")
            and current.get("source_snapshot") == expected.get("source_snapshot")
        )

    def _task_review_evidence(
        self,
        context: TaskExecutionContext,
        changes: RepositoryChangeSet,
        candidate: dict[str, Any],
        verification_evidence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        baselines = self._repository_baselines(context)
        retry_history = []
        for attempt in sorted(
            context.previous_attempts,
            key=lambda item: item.attempt_number,
        )[-16:]:
            review = attempt.metadata.get("task_review", {})
            retry_history.append(
                {
                    "attempt_number": attempt.attempt_number,
                    "status": attempt.status.value,
                    "failure_category": (
                        attempt.error_category.value
                        if attempt.error_category is not None
                        else None
                    ),
                    "task_review_approved": (
                        bool(review.get("approved"))
                        if isinstance(review, dict) and "approved" in review
                        else None
                    ),
                }
            )
        packet = {
            "schema_version": 1,
            "diff_scope": "cumulative_task",
            "attempt_number": context.attempt_number,
            "retry_workspace": baselines.retry_workspace,
            "changed_files": changes.changed_files,
            "review_files": changes.review_files,
            "commit_eligible_files": changes.commit_files,
            "task_baseline": _baseline_identity(baselines.task),
            "attempt_baseline": _baseline_identity(baselines.attempt),
            "candidate": {
                "identity_sha256": candidate.get("identity_sha256"),
                "head_commit": candidate.get("head_commit"),
                "tree_id": candidate.get("tree_id"),
                "source_snapshot": candidate.get("source_snapshot", {}),
            },
            "verifier_candidate_identity_sha256": (
                verification_evidence.get("candidate_identity_sha256")
                if isinstance(verification_evidence, dict)
                else None
            ),
            "prior_attempts": retry_history,
        }
        safe = sanitize_json(packet, max_chars=20_000)
        if not isinstance(safe, dict):
            raise RepositoryBaselineMismatch(
                "Task review evidence could not be bounded safely."
            )
        return safe

    def _source_identity_mismatch_result(
        self,
        context: TaskExecutionContext,
        before: dict[str, str],
        *,
        changed_files: list[str],
        changes: RepositoryChangeSet,
        artifacts: list[ExecutionArtifact],
        stage: str,
    ) -> TaskExecutionResult:
        return TaskExecutionResult(
            succeeded=False,
            summary="Task review stopped because candidate source identity changed.",
            artifacts=artifacts,
            failure_category=FailureCategory.RESUMABILITY_FAILURE,
            error_message=(
                "Review-eligible source changed after verification; the verifier and "
                "reviewer must evaluate the same candidate."
            ),
            retryable=False,
            verifier_status="passed",
            reviewer_status="invalidated",
            changed_files=changed_files,
            metadata={
                **self._execution_metadata(context, changes),
                "source_identity_mismatch": {"stage": stage},
            },
        )

    def _task_diff(
        self,
        context: TaskExecutionContext,
        changes: RepositoryChangeSet,
        *,
        candidate: dict[str, Any],
    ) -> str:
        baselines = self._repository_baselines(context)
        cumulative_diff = getattr(
            self.git_repository,
            "review_diff_since_baseline",
            None,
        )
        if cumulative_diff is not None and baselines.task.tree_id is not None:
            return cumulative_diff(
                baselines.task.model_dump(mode="json"),
                max_chars=30_000,
                paths=changes.review_files,
                candidate=candidate,
            )
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
        repository_intelligence: str | None,
        review_evidence: dict[str, Any],
        artifact_identifiers: list[str] | None = None,
    ) -> dict[str, Any]:
        review_task = getattr(self.reviewer, "review_task", None)
        if review_task is not None:
            arguments = {
                "original_task": context.run.original_task,
                "task_spec": plan["steps"][0],
                "expected_outputs": context.task.expected_outputs,
                "artifacts": (
                    artifact_identifiers
                    if artifact_identifiers is not None
                    else changes.review_files
                ),
                "task_diff": task_diff,
                "coder_summary": coder_summary,
                "verifier_result": verifier_result,
                "generated_artifacts": changes.generated_files,
                "ignored_files": changes.ignored_files,
                "tracked_generated_artifacts": changes.tracked_generated_files,
                "repository_intelligence": repository_intelligence,
                "review_evidence": review_evidence,
                "changed_files": changes.changed_files,
            }
            return review_task(**_supported_arguments(review_task, arguments))
        arguments = {
            "user_task": context.run.original_task,
            "plan": plan,
            "git_diff": task_diff,
            "test_output": (
                coder_summary
                if context.task.execution_kind == TaskExecutionKind.ANALYSIS
                else verifier_result.get("output")
            ),
            "repository_intelligence": repository_intelligence,
            "review_evidence": review_evidence,
        }
        return self.reviewer.review(
            **_supported_arguments(self.reviewer.review, arguments)
        )


def _drain_model_results(agent) -> list[dict[str, Any]]:
    model = getattr(agent, "model", None)
    drain = getattr(model, "drain_results", None)
    if drain is None:
        return []
    return [result.event_metadata() for result in drain()]


def _bounded_diagnostic(value: Any, max_chars: int = _RETRY_DIAGNOSTIC_MAX_CHARS) -> str:
    if value is None:
        return ""
    redacted = redact_diagnostic_text(str(value), max_chars=max_chars * 2) or ""
    if len(redacted) <= max_chars:
        return redacted
    half = max(1, (max_chars - 48) // 2)
    return (
        redacted[:half]
        + "\n[diagnostic middle truncated]\n"
        + redacted[-half:]
    )[:max_chars]


def _bounded_diagnostic_items(
    values: Any,
    *,
    max_items: int = 16,
    max_chars: int = 1_000,
) -> list[str]:
    if not isinstance(values, (list, tuple)):
        values = [values]
    bounded: list[str] = []
    for value in values[:max_items]:
        safe = sanitize_diagnostic_json(value, max_chars=max_chars)
        if isinstance(safe, str):
            text = safe
        else:
            text = json.dumps(
                safe,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        text = _bounded_diagnostic(text, max_chars).strip()
        if text:
            bounded.append(text)
    return bounded


def _bounded_command(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    bounded = []
    for item in value[:16]:
        text = _bounded_diagnostic(item, 256).strip()
        if text:
            bounded.append(text)
    return bounded


def _safe_exit_status(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bounded_verifier_failure(
    verifier_result: dict[str, Any],
    verifier_status: str,
) -> dict[str, Any]:
    stdout = verifier_result.get("stdout")
    stderr = verifier_result.get("stderr")
    if stdout is None and stderr is None:
        stdout = verifier_result.get("output")
    safe_stdout = _bounded_diagnostic(stdout)
    safe_stderr = _bounded_diagnostic(stderr)
    lines = [
        line.strip()
        for line in f"{safe_stdout}\n{safe_stderr}".splitlines()
        if line.strip() and _FAILURE_LINE_PATTERN.search(line)
    ]
    failing_tests = _bounded_diagnostic_items(
        lines,
        max_items=12,
        max_chars=512,
    )
    exception_details = _bounded_diagnostic_items(
        [
            line
            for line in lines
            if re.search(r"(?:error|exception|traceback|assert)", line, re.IGNORECASE)
        ],
        max_items=8,
        max_chars=512,
    )
    command = _bounded_command(verifier_result.get("command", []))
    exit_code = _safe_exit_status(verifier_result.get("exit_code"))
    summary = _bounded_diagnostic(
        f"Verifier status={verifier_status}; command={command}; exit_status={exit_code}",
        4_000,
    )
    return {
        "passed": False,
        "command": command,
        "exit_code": exit_code,
        "reason": _bounded_diagnostic(verifier_result.get("reason"), 2_000),
        "status": verifier_status,
        "stdout": safe_stdout,
        "stderr": safe_stderr,
        "diagnostic_summary": summary,
        "failing_tests": failing_tests,
        "exception_details": exception_details,
        "stdout_truncated": len(str(stdout or "")) > len(safe_stdout),
        "stderr_truncated": len(str(stderr or "")) > len(safe_stderr),
        "artifact_suppression_active": bool(
            verifier_result.get("artifact_suppression_active")
        ),
        "pytest_cache_disabled": bool(verifier_result.get("pytest_cache_disabled")),
    }


def _supported_arguments(callable_object, arguments: dict[str, Any]) -> dict[str, Any]:
    parameters = inspect.signature(callable_object).parameters.values()
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return arguments
    supported = {parameter.name for parameter in parameters}
    return {name: value for name, value in arguments.items() if name in supported}


def _baseline_identity(baseline: RepositoryBaseline) -> dict[str, Any]:
    return {
        "identity_sha256": baseline.identity_sha256,
        "state_sha256": baseline.state_sha256,
        "head_commit": baseline.head_commit,
        "tree_id": baseline.tree_id,
    }


def _canonical_object_id(value: object) -> str | None:
    if not isinstance(value, str) or len(value) not in {40, 64}:
        return None
    normalized = value.lower()
    return (
        normalized
        if all(character in "0123456789abcdef" for character in normalized)
        else None
    )


def _baselines_have_same_candidate_state(
    left: RepositoryBaseline,
    right: RepositoryBaseline,
) -> bool:
    if left.tree_id is not None and right.tree_id is not None:
        return left.head_commit == right.head_commit and left.tree_id == right.tree_id
    return left.state_sha256 == right.state_sha256


def _pending_tool_approval(result: TaskExecutionResult) -> dict[str, Any] | None:
    internal = result.metadata.get("_agentbus", {})
    if not isinstance(internal, dict):
        return None
    pending = internal.get("tool_approval_pending")
    return pending if isinstance(pending, dict) else None


def _bounded_capability_names(values) -> list[str]:
    return sorted(
        {
            str(getattr(value, "value", value))[:128]
            for value in values
        }
    )[:32]
