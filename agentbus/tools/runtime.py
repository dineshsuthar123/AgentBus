from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.state_store import StateStore
from agentbus.policy import ToolApprovalGrant, ToolPolicyEngine
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools.adapters import builtin_tool_registry
from agentbus.tools.budget import ToolBudgetLedger
from agentbus.tools.dispatcher import ToolDispatcher, ToolDispatchResponse
from agentbus.tools.protocol import (
    StructuredToolCall,
    ToolInvocation,
    ToolInvocationContext,
    ToolResourceBudget,
)


class ManagedToolRuntime:
    def __init__(
        self,
        *,
        workspace: str | Path,
        worktree: str | Path,
        state_store: StateStore,
        cancellation_registry: CancellationRegistry,
        executable_catalog: ExecutableCatalog,
        owned_worktree: bool = False,
        policy_engine: ToolPolicyEngine | None = None,
        budget_ledger: ToolBudgetLedger | None = None,
    ) -> None:
        self.workspace = _canonical_directory(workspace, "workspace")
        self.worktree = _canonical_directory(worktree, "worktree")
        self.state_store = state_store
        self.cancellations = cancellation_registry
        dispatcher_holder: dict[str, ToolDispatcher] = {}

        def record_output(invocation, chunk) -> None:
            dispatcher_holder["dispatcher"].record_output_chunk(invocation, chunk)

        self.registry = builtin_tool_registry(
            workspace=self.workspace,
            worktree=self.worktree,
            owned_worktree=owned_worktree,
            executable_catalog=executable_catalog,
            output_callback=record_output,
        )
        self.dispatcher = ToolDispatcher(
            self.registry,
            state_store,
            policy_engine=policy_engine,
            budget_ledger=budget_ledger,
        )
        dispatcher_holder["dispatcher"] = self.dispatcher

    def invocation_from_call(
        self,
        call: StructuredToolCall,
        *,
        run_id: str,
        task_id: str,
        caller_role: str,
        workspace_trusted: bool,
        provider_consented: bool,
        resource_budget: ToolResourceBudget | None = None,
        policy_context: dict[str, Any] | None = None,
        invocation_id: str | None = None,
    ) -> ToolInvocation:
        descriptor = self.registry.descriptor(call.tool_name)
        budget = resource_budget or ToolResourceBudget()
        cancellation = self.cancellations.get(run_id).snapshot()
        timeout = call.timeout_seconds or min(
            descriptor.maximum_timeout_seconds,
            budget.wall_clock_seconds,
        )
        return ToolInvocation(
            invocation_id=invocation_id or f"tool-{uuid4().hex}",
            run_id=run_id,
            task_id=task_id,
            tool_name=call.tool_name,
            tool_version=descriptor.version,
            arguments=call.arguments,
            requested_capabilities=call.expected_capabilities,
            context=ToolInvocationContext(
                workspace_identity=str(self.workspace),
                worktree_identity=str(self.worktree),
                caller_role=caller_role,
                workspace_trusted=workspace_trusted,
                provider_consented=provider_consented,
                policy_context=policy_context or {},
            ),
            timeout_seconds=timeout,
            resource_budget=budget,
            cancellation_revision=cancellation.revision,
            invocation_revision=call.invocation_revision,
            idempotency_key=call.idempotency_key,
        )

    def invoke(
        self,
        call: StructuredToolCall,
        *,
        run_id: str,
        task_id: str,
        caller_role: str,
        workspace_trusted: bool,
        provider_consented: bool,
        resource_budget: ToolResourceBudget | None = None,
        policy_context: dict[str, Any] | None = None,
        invocation_id: str | None = None,
    ) -> ToolDispatchResponse:
        invocation = self.invocation_from_call(
            call,
            run_id=run_id,
            task_id=task_id,
            caller_role=caller_role,
            workspace_trusted=workspace_trusted,
            provider_consented=provider_consented,
            resource_budget=resource_budget,
            policy_context=policy_context,
            invocation_id=invocation_id,
        )
        return self.dispatch(invocation)

    def dispatch(
        self,
        invocation: ToolInvocation,
        *,
        approval: ToolApprovalGrant | None = None,
    ) -> ToolDispatchResponse:
        try:
            workspace = _canonical_directory(
                invocation.context.workspace_identity,
                "invocation workspace",
            )
            worktree = _canonical_directory(
                invocation.context.worktree_identity,
                "invocation worktree",
            )
        except ValueError as exc:
            raise ValueError(
                "Invocation context does not match the managed tool runtime."
            ) from exc
        if workspace != self.workspace or worktree != self.worktree:
            raise ValueError(
                "Invocation context does not match the managed tool runtime."
            )
        return self.dispatcher.dispatch(
            invocation,
            approval=approval,
            cancellation=self.cancellations.get(invocation.run_id),
        )

    def recover_run(self, run_id: str):
        self.cancellations.recover(run_id)
        return self.dispatcher.recover_run(run_id)


def build_managed_tool_runtime(
    *,
    workspace: str | Path,
    worktree: str | Path | None = None,
    state_store: StateStore,
    cancellation_registry: CancellationRegistry | None = None,
    executable_catalog: ExecutableCatalog | None = None,
    owned_worktree: bool = False,
    policy_engine: ToolPolicyEngine | None = None,
    budget_ledger: ToolBudgetLedger | None = None,
) -> ManagedToolRuntime:
    return ManagedToolRuntime(
        workspace=workspace,
        worktree=workspace if worktree is None else worktree,
        state_store=state_store,
        cancellation_registry=(
            cancellation_registry or CancellationRegistry(state_store)
        ),
        executable_catalog=executable_catalog or ExecutableCatalog.standard(),
        owned_worktree=owned_worktree,
        policy_engine=policy_engine,
        budget_ledger=budget_ledger,
    )


def _canonical_directory(value: str | Path, label: str) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Managed tool {label} is unavailable.") from exc
    if not path.is_dir():
        raise ValueError(f"Managed tool {label} must be a directory.")
    return path
