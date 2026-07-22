from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.state_store import StateStore
from agentbus.mcp import McpImportSession, McpServerConfig
from agentbus.mcp import import_mcp_server as import_configured_mcp_server
from agentbus.policy import ToolApprovalGrant, ToolPolicyEngine
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools.adapters import builtin_tool_registry
from agentbus.tools.budget import ToolBudgetLedger
from agentbus.tools.capabilities import derive_required_capabilities
from agentbus.tools.dispatcher import ToolDispatcher, ToolDispatchResponse
from agentbus.tools.protocol import (
    StructuredToolCall,
    ToolCapabilityEscalationError,
    ToolCapabilityName,
    ToolDescriptor,
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
        source_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.workspace = _canonical_directory(workspace, "workspace")
        self.worktree = _canonical_directory(worktree, "worktree")
        self.state_store = state_store
        self.cancellations = cancellation_registry
        self.executable_catalog = executable_catalog
        self.source_environment = source_environment
        self._lifecycle_lock = threading.RLock()
        self._mcp_sessions: dict[str, McpImportSession] = {}
        self._closed = False
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

    def prepare_model_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        expected_capabilities: tuple[ToolCapabilityName, ...],
        run_id: str,
        task_id: str,
        caller_role: str,
        workspace_trusted: bool,
        provider_consented: bool,
        timeout_seconds: float | None = None,
        invocation_revision: int = 1,
        idempotency_key: str | None = None,
        resource_budget: ToolResourceBudget | None = None,
        policy_context: dict[str, Any] | None = None,
    ) -> StructuredToolCall:
        """Validate model claims before creating an authoritative scoped call."""
        descriptor = self.registry.descriptor(tool_name)
        provisional = StructuredToolCall(
            tool_name=tool_name,
            arguments=arguments,
            expected_capabilities=descriptor.capabilities,
            timeout_seconds=timeout_seconds,
            invocation_revision=invocation_revision,
            idempotency_key=idempotency_key,
        )
        invocation = self.invocation_from_call(
            provisional,
            run_id=run_id,
            task_id=task_id,
            caller_role=caller_role,
            workspace_trusted=workspace_trusted,
            provider_consented=provider_consented,
            resource_budget=resource_budget,
            policy_context=policy_context,
        )
        required = derive_required_capabilities(invocation, descriptor)
        expected_names = tuple(expected_capabilities)
        required_names = tuple(capability.name for capability in required)
        if (
            len(expected_names) != len(set(expected_names))
            or set(expected_names) != set(required_names)
        ):
            raise ToolCapabilityEscalationError(
                "Model-declared capability names do not exactly match runtime "
                "derivation."
            )
        return provisional.model_copy(update={"expected_capabilities": required})

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
        self._require_open()
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
        self._require_open()
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
        self._require_open()
        self.cancellations.recover(run_id)
        return self.dispatcher.recover_run(run_id)

    def import_mcp_server(
        self,
        config: McpServerConfig,
        *,
        run_id: str | None = None,
    ) -> tuple[ToolDescriptor, ...]:
        with self._lifecycle_lock:
            self._require_open()
            if config.server_id in self._mcp_sessions:
                raise ValueError(
                    f"MCP server is already imported: {config.server_id}."
                )
            cancellation = self.cancellations.get(run_id) if run_id else None
            session = import_configured_mcp_server(
                self.registry,
                config,
                worktree=self.worktree,
                executable_catalog=self.executable_catalog,
                source_environment=self.source_environment,
                cancellation=cancellation,
            )
            self._mcp_sessions[config.server_id] = session
            return session.descriptors

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            sessions = tuple(reversed(tuple(self._mcp_sessions.values())))
            self._mcp_sessions.clear()
        for session in sessions:
            session.close()

    def __enter__(self) -> "ManagedToolRuntime":
        self._require_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Managed tool runtime is closed.")


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
    source_environment: Mapping[str, str] | None = None,
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
        source_environment=source_environment,
    )


def _canonical_directory(value: str | Path, label: str) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Managed tool {label} is unavailable.") from exc
    if not path.is_dir():
        raise ValueError(f"Managed tool {label} must be a directory.")
    return path
