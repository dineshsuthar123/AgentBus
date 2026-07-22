from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass

from agentbus.execution.cancellation import (
    CancellationRequested,
    CancellationState,
    CancellationToken,
)
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepositoryError
from agentbus.policy import (
    ToolApprovalDisposition,
    ToolApprovalGrant,
    ToolPolicyEngine,
    build_tool_approval_request,
)
from agentbus.sandbox.errors import ProcessSupervisionError
from agentbus.security.redaction import redact_text
from agentbus.tools.budget import (
    ToolBudgetExceeded,
    ToolBudgetLedger,
    ToolBudgetReservation,
)
from agentbus.tools.capabilities import (
    anticipated_tool_usage,
    derive_required_capabilities,
    require_expected_capabilities,
    requires_process_slot,
)
from agentbus.tools.filesystem_security import FileSystemSecurityError
from agentbus.tools.interfaces import ToolExecutionOutput
from agentbus.tools.protocol import (
    ToolApprovalRequest,
    ToolAuditRecord,
    ToolCancellationSnapshot,
    ToolError,
    ToolErrorCategory,
    ToolInvocation,
    ToolInvocationStatus,
    ToolOutputChunk,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolProtocolError,
    ToolResourceUsage,
    ToolResult,
    safe_protocol_dict,
    validate_tool_output,
)
from agentbus.tools.records import (
    TERMINAL_TOOL_STATUSES,
    ToolAuditEntry,
    ToolInvocationRecord,
    build_tool_audit_record,
)
from agentbus.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolDispatchResponse:
    invocation: ToolInvocation
    record: ToolInvocationRecord
    result: ToolResult | None = None
    approval_request: ToolApprovalRequest | None = None
    audit: ToolAuditEntry | None = None
    replayed: bool = False
    in_progress: bool = False

    @property
    def awaiting_approval(self) -> bool:
        return self.approval_request is not None and self.result is None


class ToolDispatcher:
    def __init__(
        self,
        registry: ToolRegistry,
        state_store: StateStore,
        *,
        policy_engine: ToolPolicyEngine | None = None,
        budget_ledger: ToolBudgetLedger | None = None,
    ) -> None:
        self.registry = registry
        self.state_store = state_store
        self.policy_engine = policy_engine or ToolPolicyEngine()
        self.budget_ledger = budget_ledger or ToolBudgetLedger()
        self._guard = threading.RLock()
        self._run_locks: dict[str, threading.RLock] = {}
        self._restored_runs: set[str] = set()

    def dispatch(
        self,
        invocation: ToolInvocation,
        *,
        approval: ToolApprovalGrant | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ToolDispatchResponse:
        descriptor = self.registry.descriptor(
            invocation.tool_name,
            version=invocation.tool_version,
        )
        required = derive_required_capabilities(invocation, descriptor)
        require_expected_capabilities(
            invocation.requested_capabilities,
            required,
        )
        normalized = ToolInvocation.model_validate(
            invocation.model_dump(mode="python")
            | {"requested_capabilities": required}
        )
        anticipated = anticipated_tool_usage(normalized)
        process_slot = requires_process_slot(normalized)
        replayed = False

        run_lock = self._run_lock(normalized.run_id)
        with run_lock:
            self._ensure_restored(normalized.run_id)
            existing = self.state_store.find_tool_invocation_request(
                normalized,
                anticipated_usage=anticipated,
                process_slot=process_slot,
            )
            if existing is not None:
                replayed = True
                if existing.invocation_id != normalized.invocation_id:
                    normalized = ToolInvocation.model_validate(
                        normalized.model_dump(mode="python")
                        | {"invocation_id": existing.invocation_id}
                    )
                terminal = self._terminal_response(
                    normalized,
                    existing,
                    replayed=True,
                )
                if terminal is not None:
                    return terminal
                if existing.status == ToolInvocationStatus.RUNNING:
                    return ToolDispatchResponse(
                        invocation=normalized,
                        record=existing,
                        replayed=True,
                        in_progress=True,
                    )
            if cancellation is not None:
                cancellation.checkpoint(
                    "tool-dispatcher",
                    stage="before-policy",
                )

            try:
                reservation = self.budget_ledger.begin(
                    normalized,
                    anticipated_usage=anticipated,
                    process_slot=process_slot,
                )
            except ToolBudgetExceeded as exc:
                self.state_store.record_event(
                    normalized.run_id,
                    "tool_budget_rejected",
                    {
                        "invocation_id": normalized.invocation_id,
                        "invocation_revision": normalized.invocation_revision,
                        "tool_name": normalized.tool_name,
                        "limit_name": exc.limit_name,
                    },
                    task_id=normalized.task_id,
                )
                raise
            try:
                record = existing or self.state_store.record_tool_invocation(
                    normalized,
                    anticipated_usage=anticipated,
                    process_slot=process_slot,
                )
            except Exception:
                if not reservation.duplicate:
                    self.budget_ledger.abort(reservation)
                raise

            policy_response = self._authorize(
                normalized,
                record,
                reservation,
                approval=approval,
                cancellation=cancellation,
                replayed=replayed,
            )
            if policy_response is not None:
                return policy_response

            record = self.state_store.get_tool_invocation(
                normalized.run_id,
                normalized.invocation_id,
            )
            try:
                if cancellation is not None:
                    cancellation.checkpoint(
                        "tool-dispatcher",
                        stage="before-tool-start",
                    )
            except CancellationRequested as exc:
                return self._cancel_before_execution(
                    normalized,
                    record,
                    reservation,
                    exc.state,
                    replayed=replayed,
                )
            if process_slot:
                self.budget_ledger.activate_process(reservation)
            record = self.state_store.mark_tool_invocation_started(
                normalized.run_id,
                normalized.invocation_id,
                approval_id=record.approval_id,
            )

        return self._execute(
            normalized,
            record,
            reservation,
            cancellation=cancellation,
            replayed=replayed,
        )

    def recover_run(self, run_id: str) -> tuple[ToolInvocationRecord, ...]:
        with self._run_lock(run_id):
            with self._guard:
                if run_id in self._restored_runs:
                    raise RuntimeError(
                        "Tool run recovery must occur before budget restoration."
                    )
            reconciled = tuple(
                self.state_store.reconcile_running_tool_invocations(run_id)
            )
            after = 0
            while True:
                records = self.state_store.list_tool_invocations(
                    run_id,
                    after_sequence=after,
                    limit=1000,
                )
                for record in records:
                    error = record.safe_result.error if record.safe_result else None
                    if error is not None and error.code in {
                        "restart_cancelled",
                        "restart_interrupted",
                    }:
                        self._audit_reconciled_record(record)
                if len(records) < 1000:
                    break
                after = records[-1].invocation_sequence
            self._ensure_restored(run_id)
            return reconciled

    def record_output_chunk(
        self,
        invocation: ToolInvocation,
        chunk: ToolOutputChunk,
    ) -> None:
        record = self.state_store.get_tool_invocation(
            invocation.run_id,
            invocation.invocation_id,
        )
        if (
            record.status != ToolInvocationStatus.RUNNING
            or record.task_id != invocation.task_id
            or record.tool_name != invocation.tool_name
            or record.invocation_revision != invocation.invocation_revision
        ):
            raise RuntimeError(
                "Tool output can be recorded only for its running invocation."
            )
        payload = safe_protocol_dict(chunk)
        payload.update(
            {
                "invocation_id": invocation.invocation_id,
                "invocation_revision": invocation.invocation_revision,
            }
        )
        self.state_store.record_event(
            invocation.run_id,
            "tool_output_chunk",
            payload,
            task_id=invocation.task_id,
        )

    def discard_run(self, run_id: str) -> None:
        with self._guard:
            self._run_locks.pop(run_id, None)
            self._restored_runs.discard(run_id)

    def _authorize(
        self,
        invocation: ToolInvocation,
        record: ToolInvocationRecord,
        reservation: ToolBudgetReservation,
        *,
        approval: ToolApprovalGrant | None,
        cancellation: CancellationToken | None,
        replayed: bool,
    ) -> ToolDispatchResponse | None:
        descriptor = self.registry.descriptor(
            invocation.tool_name,
            version=invocation.tool_version,
        )
        if record.status == ToolInvocationStatus.AWAITING_APPROVAL:
            request_record = self.state_store.get_tool_approval(
                invocation.run_id,
                record.approval_id or "missing-approval",
            )
            request = request_record.request
            grant = approval or _grant_from_record(request_record)
            if grant is None:
                return ToolDispatchResponse(
                    invocation=invocation,
                    record=record,
                    approval_request=request,
                    replayed=replayed,
                )
            self.state_store.record_tool_approval_grant(
                grant,
                invocation,
                descriptor,
            )
            decision = self.policy_engine.evaluate(
                invocation,
                descriptor,
                approval=grant,
            )
            record = self.state_store.record_tool_policy_decision(
                invocation.run_id,
                decision,
                approval_id=grant.approval_id,
            )
            if decision.outcome == ToolPolicyOutcome.DENY:
                self.budget_ledger.abort(reservation)
                return self._denied_response(
                    invocation,
                    record,
                    decision,
                    replayed=replayed,
                )
            return None

        if record.status != ToolInvocationStatus.REQUESTED:
            raise RuntimeError(
                f"Tool dispatcher cannot authorize state '{record.status.value}'."
            )
        if cancellation is not None:
            cancellation.checkpoint(
                "tool-dispatcher",
                stage="before-policy-evaluation",
            )
        decision = self.policy_engine.evaluate(invocation, descriptor)
        if decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
            request = build_tool_approval_request(
                invocation,
                descriptor,
                decision,
            )
            record = self.state_store.record_tool_policy_decision(
                invocation.run_id,
                decision,
                approval_id=request.approval_id,
            )
            self.state_store.record_tool_approval_request(
                request,
                invocation,
                descriptor,
            )
            return ToolDispatchResponse(
                invocation=invocation,
                record=record,
                approval_request=request,
                replayed=replayed,
            )
        record = self.state_store.record_tool_policy_decision(
            invocation.run_id,
            decision,
        )
        if decision.outcome == ToolPolicyOutcome.DENY:
            self.budget_ledger.abort(reservation)
            return self._denied_response(
                invocation,
                record,
                decision,
                replayed=replayed,
            )
        return None

    def _execute(
        self,
        invocation: ToolInvocation,
        record: ToolInvocationRecord,
        reservation: ToolBudgetReservation,
        *,
        cancellation: CancellationToken | None,
        replayed: bool,
    ) -> ToolDispatchResponse:
        started = time.monotonic()
        try:
            output = self.registry.resolve(
                invocation.tool_name,
                version=invocation.tool_version,
            ).execute(invocation, cancellation=cancellation)
            validate_tool_output(
                output.structured_output,
                self.registry.descriptor(
                    invocation.tool_name,
                    version=invocation.tool_version,
                ),
            )
            usage = output.resource_usage.model_copy(
                update={
                    "wall_clock_seconds": max(
                        output.resource_usage.wall_clock_seconds,
                        time.monotonic() - started,
                    )
                }
            )
            try:
                self.budget_ledger.complete(reservation, usage)
            except ToolBudgetExceeded as exc:
                if output.timed_out or output.cancelled:
                    result = self._result_from_output(
                        invocation,
                        record.policy_decision,
                        record.approval_id,
                        output,
                        usage,
                        cancellation,
                    )
                else:
                    result = self._failed_result(
                        invocation,
                        record.policy_decision,
                        record.approval_id,
                        category=ToolErrorCategory.RESOURCE_EXHAUSTED,
                        code=f"budget_{exc.limit_name}",
                        message=str(exc),
                        usage=usage,
                        output=output,
                    )
            else:
                result = self._result_from_output(
                    invocation,
                    record.policy_decision,
                    record.approval_id,
                    output,
                    usage,
                    cancellation,
                )
        except CancellationRequested as exc:
            usage = ToolResourceUsage(
                wall_clock_seconds=max(0.0, time.monotonic() - started)
            )
            self.budget_ledger.complete(reservation, usage)
            result = self._cancelled_result(
                invocation,
                record.policy_decision,
                record.approval_id,
                exc.state,
                usage=usage,
                cleanup_completed=True,
            )
        except Exception as exc:
            elapsed = max(0.0, time.monotonic() - started)
            usage = reservation.anticipated_usage.model_copy(
                update={"wall_clock_seconds": elapsed}
            )
            budget_error: ToolBudgetExceeded | None = None
            try:
                self.budget_ledger.complete(reservation, usage)
            except ToolBudgetExceeded as violation:
                budget_error = violation
            category, code = _exception_category(exc)
            if budget_error is not None:
                category = ToolErrorCategory.RESOURCE_EXHAUSTED
                code = f"budget_{budget_error.limit_name}"
            result = self._failed_result(
                invocation,
                record.policy_decision,
                record.approval_id,
                category=category,
                code=code,
                message=str(budget_error or exc),
                usage=usage,
            )
        return self._complete(
            invocation,
            result,
            replayed=replayed,
        )

    def _complete(
        self,
        invocation: ToolInvocation,
        result: ToolResult,
        *,
        replayed: bool,
    ) -> ToolDispatchResponse:
        if result.cancellation.requested:
            self.state_store.record_event(
                invocation.run_id,
                "tool_cancel_requested",
                {
                    "invocation_id": invocation.invocation_id,
                    "invocation_revision": invocation.invocation_revision,
                    "cancellation_revision": result.cancellation.revision,
                    "signal_sent": result.cancellation.signal_sent,
                },
                task_id=invocation.task_id,
            )
            if result.cancellation.acknowledged:
                self.state_store.record_event(
                    invocation.run_id,
                    "tool_cancel_acknowledged",
                    {
                        "invocation_id": invocation.invocation_id,
                        "invocation_revision": invocation.invocation_revision,
                        "process_terminated": (
                            result.cancellation.process_terminated
                        ),
                    },
                    task_id=invocation.task_id,
                )
        for artifact in result.artifacts:
            self.state_store.record_event(
                invocation.run_id,
                "tool_artifact_created",
                {
                    "invocation_id": invocation.invocation_id,
                    "invocation_revision": invocation.invocation_revision,
                    "artifact": safe_protocol_dict(artifact),
                },
                task_id=invocation.task_id,
            )
        record = self.state_store.complete_tool_invocation(
            invocation.run_id,
            result,
        )
        if result.cancellation.cleanup_completed:
            self.state_store.record_event(
                invocation.run_id,
                "tool_cleanup_completed",
                {
                    "invocation_id": invocation.invocation_id,
                    "invocation_revision": invocation.invocation_revision,
                    "process_terminated": result.cancellation.process_terminated,
                },
                task_id=invocation.task_id,
            )
        audit = self.state_store.record_tool_audit(
            build_tool_audit_record(
                invocation,
                result,
                started_at=record.started_at,
                completed_at=record.completed_at,
                affected_resource_hashes=_affected_resource_hashes(result),
            )
        )
        return ToolDispatchResponse(
            invocation=invocation,
            record=record,
            result=result,
            audit=audit,
            replayed=replayed,
        )

    def _cancel_before_execution(
        self,
        invocation: ToolInvocation,
        record: ToolInvocationRecord,
        reservation: ToolBudgetReservation,
        state: CancellationState,
        *,
        replayed: bool,
    ) -> ToolDispatchResponse:
        record = self.state_store.mark_tool_invocation_started(
            invocation.run_id,
            invocation.invocation_id,
            approval_id=record.approval_id,
        )
        self.budget_ledger.abort(reservation)
        result = self._cancelled_result(
            invocation,
            record.policy_decision,
            record.approval_id,
            state,
            usage=ToolResourceUsage(),
            cleanup_completed=True,
        )
        return self._complete(invocation, result, replayed=replayed)

    def _denied_response(
        self,
        invocation: ToolInvocation,
        record: ToolInvocationRecord,
        decision: ToolPolicyDecision,
        *,
        replayed: bool,
    ) -> ToolDispatchResponse:
        category = (
            ToolErrorCategory.APPROVAL_INVALID
            if decision.rule_id == "deny.invalid_approval"
            else ToolErrorCategory.POLICY_DENIED
        )
        result = ToolResult(
            invocation_id=invocation.invocation_id,
            invocation_revision=invocation.invocation_revision,
            status=ToolInvocationStatus.DENIED,
            error=ToolError(
                category=category,
                code=decision.rule_id.replace(".", "_"),
                message=decision.reason,
                retryable=False,
            ),
            cancellation=ToolCancellationSnapshot(
                revision=invocation.cancellation_revision
            ),
            policy_decision=decision,
            approval_id=record.approval_id,
        )
        audit = self.state_store.record_tool_audit(
            build_tool_audit_record(
                invocation,
                result,
                started_at=None,
                completed_at=record.completed_at,
            )
        )
        return ToolDispatchResponse(
            invocation=invocation,
            record=record,
            result=result,
            audit=audit,
            replayed=replayed,
        )

    def _terminal_response(
        self,
        invocation: ToolInvocation,
        record: ToolInvocationRecord,
        *,
        replayed: bool,
    ) -> ToolDispatchResponse | None:
        if record.status not in TERMINAL_TOOL_STATUSES:
            return None
        result = record.safe_result
        if result is None and record.policy_decision is not None:
            return self._denied_response(
                invocation,
                record,
                record.policy_decision,
                replayed=replayed,
            )
        return ToolDispatchResponse(
            invocation=invocation,
            record=record,
            result=result,
            replayed=replayed,
        )

    def _result_from_output(
        self,
        invocation: ToolInvocation,
        decision: ToolPolicyDecision | None,
        approval_id: str | None,
        output: ToolExecutionOutput,
        usage: ToolResourceUsage,
        cancellation: CancellationToken | None,
    ) -> ToolResult:
        policy = _require_policy(decision)
        cancellation_state = cancellation.snapshot() if cancellation else None
        snapshot = _cancellation_snapshot(
            invocation,
            cancellation_state,
            process_terminated=output.cancelled,
            cleanup_completed=output.cancelled,
            operation_completed_after_request=(
                bool(cancellation_state and cancellation_state.requested)
                and not output.cancelled
            ),
        )
        if output.cancelled:
            return self._cancelled_result(
                invocation,
                policy,
                approval_id,
                cancellation_state or CancellationState(requested=True),
                usage=usage,
                output=output,
                cleanup_completed=True,
            )
        if output.timed_out:
            return self._failed_result(
                invocation,
                policy,
                approval_id,
                category=ToolErrorCategory.TIMED_OUT,
                code="wall_clock_timeout",
                message="Tool execution exceeded its wall-clock timeout.",
                usage=usage,
                output=output,
                status=ToolInvocationStatus.TIMED_OUT,
                timed_out=True,
                cancellation_snapshot=snapshot,
            )
        if output.exit_code not in {None, 0}:
            return self._failed_result(
                invocation,
                policy,
                approval_id,
                category=ToolErrorCategory.PROCESS,
                code="nonzero_exit",
                message="Managed process exited unsuccessfully.",
                usage=usage,
                output=output,
                cancellation_snapshot=snapshot,
            )
        return ToolResult(
            invocation_id=invocation.invocation_id,
            invocation_revision=invocation.invocation_revision,
            status=ToolInvocationStatus.SUCCEEDED,
            structured_output=output.structured_output,
            stdout=output.stdout,
            stderr=output.stderr,
            stdout_truncated=output.stdout_truncated,
            stderr_truncated=output.stderr_truncated,
            artifacts=output.artifacts,
            exit_code=output.exit_code,
            duration_seconds=usage.wall_clock_seconds,
            cancellation=snapshot,
            resource_usage=usage,
            policy_decision=policy,
            approval_id=approval_id,
            safe_diagnostic_metadata=output.safe_diagnostic_metadata,
        )

    def _failed_result(
        self,
        invocation: ToolInvocation,
        decision: ToolPolicyDecision | None,
        approval_id: str | None,
        *,
        category: ToolErrorCategory,
        code: str,
        message: str,
        usage: ToolResourceUsage,
        output: ToolExecutionOutput | None = None,
        status: ToolInvocationStatus = ToolInvocationStatus.FAILED,
        timed_out: bool = False,
        cancellation_snapshot: ToolCancellationSnapshot | None = None,
    ) -> ToolResult:
        safe_message = redact_text(message, max_chars=2_000) or "Tool execution failed."
        return ToolResult(
            invocation_id=invocation.invocation_id,
            invocation_revision=invocation.invocation_revision,
            status=status,
            structured_output=output.structured_output if output else {},
            stdout=output.stdout if output else "",
            stderr=output.stderr if output else "",
            stdout_truncated=output.stdout_truncated if output else False,
            stderr_truncated=output.stderr_truncated if output else False,
            artifacts=output.artifacts if output else (),
            error=ToolError(
                category=category,
                code=code,
                message=safe_message,
                retryable=False,
            ),
            exit_code=output.exit_code if output else None,
            duration_seconds=usage.wall_clock_seconds,
            timed_out=timed_out,
            cancellation=(
                cancellation_snapshot
                or ToolCancellationSnapshot(
                    revision=invocation.cancellation_revision
                )
            ),
            resource_usage=usage,
            policy_decision=_require_policy(decision),
            approval_id=approval_id,
            safe_diagnostic_metadata=(
                output.safe_diagnostic_metadata if output else {}
            ),
        )

    def _cancelled_result(
        self,
        invocation: ToolInvocation,
        decision: ToolPolicyDecision | None,
        approval_id: str | None,
        state: CancellationState,
        *,
        usage: ToolResourceUsage,
        output: ToolExecutionOutput | None = None,
        cleanup_completed: bool,
    ) -> ToolResult:
        return ToolResult(
            invocation_id=invocation.invocation_id,
            invocation_revision=invocation.invocation_revision,
            status=ToolInvocationStatus.CANCELLED,
            structured_output=output.structured_output if output else {},
            stdout=output.stdout if output else "",
            stderr=output.stderr if output else "",
            stdout_truncated=output.stdout_truncated if output else False,
            stderr_truncated=output.stderr_truncated if output else False,
            artifacts=output.artifacts if output else (),
            error=ToolError(
                category=ToolErrorCategory.CANCELLED,
                code="cancellation_requested",
                message=(
                    redact_text(state.reason, max_chars=2_000)
                    or "Tool execution was cancelled."
                ),
                retryable=False,
            ),
            exit_code=output.exit_code if output else None,
            duration_seconds=usage.wall_clock_seconds,
            cancellation=_cancellation_snapshot(
                invocation,
                state,
                process_terminated=bool(output and output.cancelled),
                cleanup_completed=cleanup_completed,
            ),
            resource_usage=usage,
            policy_decision=_require_policy(decision),
            approval_id=approval_id,
            safe_diagnostic_metadata=(
                output.safe_diagnostic_metadata if output else {}
            ),
        )

    def _ensure_restored(self, run_id: str) -> None:
        with self._guard:
            if run_id in self._restored_runs:
                return
            after = 0
            while True:
                records = self.state_store.list_tool_invocations(
                    run_id,
                    after_sequence=after,
                    limit=1000,
                )
                for record in records:
                    self.budget_ledger.restore(record)
                if len(records) < 1000:
                    break
                after = records[-1].invocation_sequence
            self._restored_runs.add(run_id)

    def _audit_reconciled_record(self, record: ToolInvocationRecord) -> None:
        result = record.safe_result
        policy = record.policy_decision
        if result is None or policy is None or record.completed_at is None:
            raise RuntimeError(
                "Reconciled tool invocation is missing terminal audit metadata."
            )
        digest = hashlib.sha256(
            f"{record.invocation_id}:{record.invocation_revision}".encode("utf-8")
        ).hexdigest()[:32]
        self.state_store.record_tool_audit(
            ToolAuditRecord(
                audit_id=f"tool-audit-recovery-{digest}",
                invocation_id=record.invocation_id,
                invocation_revision=record.invocation_revision,
                run_id=record.run_id,
                task_id=record.task_id,
                tool_name=record.tool_name,
                tool_version=record.tool_version,
                protocol_version=record.protocol_version,
                caller_role=record.caller_role,
                capabilities=record.capabilities,
                policy_decision=policy,
                approval_id=record.approval_id,
                arguments_sha256=record.arguments_sha256,
                affected_resource_hashes=_affected_resource_hashes(result),
                started_at=record.started_at,
                completed_at=record.completed_at,
                cancellation=record.cancellation,
                timed_out=result.timed_out,
                resource_usage=record.resource_usage,
                artifacts=result.artifacts,
                outcome=record.status,
                error_category=record.error_category,
                created_at=record.completed_at,
            )
        )

    def _run_lock(self, run_id: str) -> threading.RLock:
        with self._guard:
            return self._run_locks.setdefault(run_id, threading.RLock())


def _grant_from_record(record) -> ToolApprovalGrant | None:
    if record.disposition is None or record.decided_at is None:
        return None
    return ToolApprovalGrant(
        approval_id=record.approval_id,
        request=record.request,
        disposition=ToolApprovalDisposition(record.disposition),
        binding_sha256=record.binding_sha256,
        reason=record.reason,
        decided_at=record.decided_at,
    )


def _require_policy(decision: ToolPolicyDecision | None) -> ToolPolicyDecision:
    if decision is None:
        raise RuntimeError("Managed tool execution requires a policy decision.")
    return decision


def _cancellation_snapshot(
    invocation: ToolInvocation,
    state: CancellationState | None,
    *,
    process_terminated: bool = False,
    cleanup_completed: bool = False,
    operation_completed_after_request: bool = False,
) -> ToolCancellationSnapshot:
    if state is None:
        return ToolCancellationSnapshot(revision=invocation.cancellation_revision)
    return ToolCancellationSnapshot(
        requested=state.requested,
        revision=state.revision,
        requested_at=state.requested_at,
        signal_sent=process_terminated,
        acknowledged=state.acknowledged,
        process_terminated=process_terminated,
        operation_completed_after_request=operation_completed_after_request,
        cleanup_completed=cleanup_completed,
        reason=redact_text(state.reason, max_chars=1_000),
    )


def _exception_category(
    exc: Exception,
) -> tuple[ToolErrorCategory, str]:
    if isinstance(exc, ToolBudgetExceeded):
        return ToolErrorCategory.RESOURCE_EXHAUSTED, f"budget_{exc.limit_name}"
    if isinstance(exc, FileSystemSecurityError):
        return ToolErrorCategory.FILESYSTEM, "filesystem_error"
    if isinstance(exc, GitRepositoryError):
        return ToolErrorCategory.GIT, "git_error"
    if isinstance(exc, ProcessSupervisionError):
        return ToolErrorCategory.PROCESS, "process_error"
    if isinstance(exc, ToolProtocolError):
        return ToolErrorCategory.PROTOCOL, "protocol_error"
    return ToolErrorCategory.INTERNAL, "internal_error"


def _affected_resource_hashes(result: ToolResult) -> dict[str, str]:
    return {
        artifact.relative_path or artifact.artifact_id: artifact.sha256
        for artifact in result.artifacts
    }
