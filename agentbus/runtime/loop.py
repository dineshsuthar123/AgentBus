import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

from agentbus.config import AgentBusConfig
from agentbus.execution.cancellation import CancellationRequested, CancellationToken
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.models import (
    RunRecord,
    RunStatus,
    TaskSpec,
    TaskStatus,
)
from agentbus.execution.state_store import (
    RunNotFoundError,
    StateStore,
)
from agentbus.memory.run_log import RunLogger
from agentbus.models.errors import (
    ModelCancellationError,
    ModelOutputError,
    ModelProviderError,
)
from agentbus.models.router import ModelRouter, model_request_context
from agentbus.models.types import ModelRole
from agentbus.runtime.prompts import SYSTEM_PROMPT
from agentbus.runtime.schemas import AgentAction
from agentbus.tools.protocol import (
    ToolCapabilityEscalationError,
    ToolResourceBudget,
)
from agentbus.tools.runtime import ManagedToolRuntime, build_managed_tool_runtime


class ManagedToolApprovalRequired(RuntimeError):
    """Signals that a managed task must suspend for exact tool approval."""

    def __init__(self, *, approval_id: str, invocation_id: str, tool_name: str):
        super().__init__(f"Tool '{tool_name}' is awaiting scoped approval.")
        self.approval_id = approval_id
        self.invocation_id = invocation_id
        self.tool_name = tool_name


class AgentLoop:
    def __init__(
        self,
        workspace: str | None = None,
        model_name: str | None = None,
        max_history_chars: int | None = None,
        config: AgentBusConfig | None = None,
        model=None,
        model_router: ModelRouter | None = None,
        cancellation: CancellationToken | None = None,
        tool_runtime: ManagedToolRuntime | None = None,
        state_store: StateStore | None = None,
        cancellation_registry: CancellationRegistry | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        workspace_trusted: bool = True,
        provider_consented: bool = True,
        resource_budget: ToolResourceBudget | None = None,
        policy_context: dict[str, Any] | None = None,
    ):
        config = config or AgentBusConfig.from_env()
        config = config.with_overrides(
            model_name=model_name,
            workspace_dir=workspace,
            max_steps=None,
        )

        self.config = config
        config.workspace_path.mkdir(parents=True, exist_ok=True)
        self.workspace = str(config.workspace_path)
        self.model_router = model_router
        if model is not None:
            self.model = model
        else:
            self.model_router = model_router or ModelRouter(config)
            self.model = self.model_router.for_role(ModelRole.CODER)
        self.logger = RunLogger(log_dir=config.runs_dir)
        self.max_history_chars = max_history_chars or config.max_history_chars
        self.cancellation = cancellation
        self.tool_runtime = tool_runtime
        self.state_store = state_store
        self.cancellation_registry = cancellation_registry
        self.run_id = run_id
        self.task_id = task_id
        self.workspace_trusted = workspace_trusted
        self.provider_consented = provider_consented
        self.resource_budget = resource_budget
        self.policy_context = dict(policy_context or {})
        self._owns_state_records = False
        self._owns_tool_runtime = False

    def run(self, user_task: str, max_steps: int | None = None) -> str:
        self._ensure_tool_runtime(user_task)
        try:
            return self._run_steps(user_task, max_steps=max_steps)
        finally:
            if self._owns_tool_runtime and self.tool_runtime is not None:
                runtime = self.tool_runtime
                self.tool_runtime = None
                self._owns_tool_runtime = False
                runtime.close()

    def _run_steps(self, user_task: str, max_steps: int | None = None) -> str:
        history = ""
        max_steps = max_steps or self.config.max_steps

        self.logger.log(
            "run_started",
            {
                "task_chars": len(user_task),
                "workspace": self.workspace,
            },
        )

        for step in range(1, max_steps + 1):
            self._checkpoint(f"before-step-{step}")
            self.logger.log("step_started", {
                "step": step,
            })

            prompt = self._build_prompt(user_task, history)

            raw_action = None
            action = None

            try:
                method = self.model.generate_json
                with model_request_context(cancellation=self.cancellation):
                    if _accepts_schema(method):
                        raw_action = method(prompt, schema=AgentAction)
                    else:
                        raw_action = method(prompt)
                self._checkpoint(f"after-model-step-{step}")
                action = AgentAction(**raw_action)
            except (CancellationRequested, ModelCancellationError):
                raise
            except ModelOutputError as error:
                observation = f"Model output error: {error.safe_message}"
                self.logger.log(
                    "model_error",
                    {"step": step, **error.safe_metadata()},
                )
            except ModelProviderError as error:
                self.logger.log(
                    "model_error",
                    {"step": step, **error.safe_metadata()},
                )
                raise
            except Exception as e:
                observation = f"Model output error: {str(e)}"
                self.logger.log(
                    "model_error",
                    {
                        "step": step,
                        "error_type": type(e).__name__,
                        "error_chars": len(str(e)),
                    },
                )
            else:
                self.logger.log(
                    "model_action",
                    {"step": step, **_action_log_metadata(action)},
                )

                try:
                    self._checkpoint(f"before-tool-step-{step}")
                    observation = self._execute(action, step=step)
                    self._checkpoint(f"after-tool-step-{step}")
                except (CancellationRequested, ModelCancellationError):
                    raise
                except ManagedToolApprovalRequired:
                    self._mark_standalone_waiting_for_approval()
                    raise
                except Exception as e:
                    observation = f"Tool error: {str(e)}"

                self.logger.log(
                    "tool_observation",
                    {
                        "step": step,
                        "observation_chars": len(observation),
                    },
                )

            history += self._format_history(step, raw_action, observation)
            history = self._trim_history(history)

            if action and action.action == "finish":
                self.logger.log("run_finished", {
                    "summary_chars": len(action.summary or ""),
                })
                self._finish_standalone(succeeded=True)
                return action.summary

        final = "Stopped because max_steps was reached. Check run logs for details."

        self.logger.log("run_stopped", {
            "reason": "max_steps_reached",
            "history_chars": len(history),
        })
        self._finish_standalone(
            succeeded=False,
            reason="The bounded model tool loop reached its step limit.",
        )

        return final

    def _build_prompt(self, user_task: str, history: str) -> str:
        return f"""
{SYSTEM_PROMPT}

User task:
{user_task}

Previous observations:
{history if history.strip() else "No previous observations."}

Managed tool catalog:
{self._tool_catalog_json()}

Return the next JSON action.
"""

    def _execute(self, action: AgentAction, *, step: int) -> str:
        if action.action == "finish":
            return f"Finished: {action.summary}"
        if self.tool_runtime is None or action.tool_call is None:
            raise RuntimeError("Managed tool runtime is unavailable.")
        if self.run_id is None or self.task_id is None:
            raise RuntimeError("Managed tool execution requires run and task IDs.")

        requested = action.tool_call
        planned_capabilities = self.policy_context.get("planned_capabilities")
        if isinstance(planned_capabilities, list) and planned_capabilities:
            declared = {
                str(getattr(value, "value", value))
                for value in planned_capabilities
            }
            requested_names = {
                capability.value for capability in requested.expected_capabilities
            }
            if not requested_names.issubset(declared):
                raise ToolCapabilityEscalationError(
                    "Tool call exceeds the planner-declared capability requirements."
                )
        call = self.tool_runtime.prepare_model_call(
            tool_name=requested.tool_name,
            arguments=requested.arguments,
            expected_capabilities=requested.expected_capabilities,
            run_id=self.run_id,
            task_id=self.task_id,
            caller_role="coder",
            workspace_trusted=self.workspace_trusted,
            provider_consented=self.provider_consented,
            timeout_seconds=requested.timeout_seconds,
            invocation_revision=requested.invocation_revision,
            idempotency_key=requested.idempotency_key,
            resource_budget=self.resource_budget,
            policy_context=self.policy_context,
        )
        response = self.tool_runtime.invoke(
            call,
            run_id=self.run_id,
            task_id=self.task_id,
            caller_role="coder",
            workspace_trusted=self.workspace_trusted,
            provider_consented=self.provider_consented,
            resource_budget=self.resource_budget,
            policy_context=self.policy_context,
            invocation_id=self._invocation_id(
                requested.idempotency_key,
                requested.invocation_revision,
            ),
        )
        if response.awaiting_approval:
            request = response.approval_request
            assert request is not None
            raise ManagedToolApprovalRequired(
                approval_id=request.approval_id,
                invocation_id=response.invocation.invocation_id,
                tool_name=response.invocation.tool_name,
            )
        if response.in_progress:
            return json.dumps(
                {
                    "status": "in_progress",
                    "invocation_id": response.invocation.invocation_id,
                    "tool_name": response.invocation.tool_name,
                },
                sort_keys=True,
            )
        if response.result is None:
            raise RuntimeError("Managed tool dispatch returned no terminal result.")
        result = response.result
        return json.dumps(
            {
                "status": result.status.value,
                "invocation_id": result.invocation_id,
                "tool_name": response.invocation.tool_name,
                "structured_output": result.structured_output,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "stdout_truncated": result.stdout_truncated,
                "stderr_truncated": result.stderr_truncated,
                "exit_code": result.exit_code,
                "error": (
                    result.error.model_dump(mode="json") if result.error else None
                ),
                "artifacts": [
                    artifact.model_dump(mode="json") for artifact in result.artifacts
                ],
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )

    def _ensure_tool_runtime(self, user_task: str) -> None:
        if self.tool_runtime is not None:
            if Path(self.workspace).resolve() != self.tool_runtime.worktree:
                raise ValueError(
                    "Agent loop workspace does not match its managed tool runtime."
                )
            if self.run_id is None or self.task_id is None:
                raise ValueError(
                    "A supplied managed tool runtime requires run and task IDs."
                )
            if self.cancellation is None:
                self.cancellation = self.tool_runtime.cancellations.get(self.run_id)
            return

        store = self.state_store or StateStore(self.config.state_database_path)
        run_id = self.run_id or self.logger.run_id
        task_id = self.task_id or "single-task"
        try:
            store.get_run(run_id)
        except RunNotFoundError:
            if self.run_id is not None or self.task_id is not None:
                raise
            store.create_run_with_tasks(
                RunRecord(
                    run_id=run_id,
                    original_task=user_task,
                    workflow_type="single",
                    model=self.config.model_name,
                    workspace=self.workspace,
                    metadata={"managed_tool_runtime": True},
                ),
                [
                    TaskSpec(
                        task_id=task_id,
                        title="Execute managed local task",
                        description=user_task,
                        assigned_role="coder",
                        maximum_attempts=1,
                    )
                ],
            )
            store.update_run_status(run_id, RunStatus.RUNNING)
            store.update_task_status(run_id, task_id, TaskStatus.READY)
            store.update_task_status(run_id, task_id, TaskStatus.RUNNING)
            self._owns_state_records = True
        else:
            store.get_task(run_id, task_id)

        registry = self.cancellation_registry or CancellationRegistry(store)
        if self.cancellation is not None:
            registry.register(run_id, self.cancellation, persist_current=True)
        else:
            self.cancellation = registry.get(run_id)
        self.state_store = store
        self.cancellation_registry = registry
        self.run_id = run_id
        self.task_id = task_id
        self.tool_runtime = build_managed_tool_runtime(
            workspace=self.workspace,
            state_store=store,
            cancellation_registry=registry,
            mcp_server_configs=self.config.mcp_server_configs,
            mcp_run_id=run_id,
        )
        self._owns_tool_runtime = True

    def _tool_catalog_json(self) -> str:
        if self.tool_runtime is None:
            return "[]"
        catalog = [
            {
                "name": descriptor.name,
                "version": str(descriptor.version),
                "description": descriptor.description,
                "expected_capabilities": [
                    capability.name.value for capability in descriptor.capabilities
                ],
                "argument_schema": descriptor.argument_schema,
            }
            for descriptor in self.tool_runtime.registry.descriptors()
        ]
        return json.dumps(catalog, ensure_ascii=False, sort_keys=True)

    def _invocation_id(self, idempotency_key: str, revision: int) -> str:
        material = (
            f"{self.run_id}\0{self.task_id}\0{idempotency_key}\0{revision}"
        ).encode("utf-8")
        return f"tool-{hashlib.sha256(material).hexdigest()[:40]}"

    def _mark_standalone_waiting_for_approval(self) -> None:
        if not self._owns_state_records or self.state_store is None:
            return
        assert self.run_id is not None and self.task_id is not None
        self.state_store.update_task_status(
            self.run_id,
            self.task_id,
            TaskStatus.WAITING_FOR_APPROVAL,
            event_type="tool_approval_suspended_task",
        )
        self.state_store.update_run_status(
            self.run_id,
            RunStatus.WAITING_FOR_APPROVAL,
            event_type="tool_approval_suspended_run",
        )

    def _finish_standalone(
        self,
        *,
        succeeded: bool,
        reason: str | None = None,
    ) -> None:
        if not self._owns_state_records or self.state_store is None:
            return
        assert self.run_id is not None and self.task_id is not None
        self.state_store.update_task_status(
            self.run_id,
            self.task_id,
            TaskStatus.SUCCEEDED if succeeded else TaskStatus.FAILED,
        )
        self.state_store.update_run_status(
            self.run_id,
            RunStatus.SUCCEEDED if succeeded else RunStatus.FAILED,
            failure_reason=reason,
        )

    def _checkpoint(self, stage: str) -> None:
        if self.cancellation is not None:
            self.cancellation.checkpoint("coder-loop", stage=stage)

    def _format_history(self, step: int, raw_action, observation: str) -> str:
        return (
            f"\n--- Step {step} ---\n"
            f"Action:\n{json.dumps(raw_action, indent=2, ensure_ascii=False)}\n"
            f"Observation:\n{observation}\n"
        )

    def _trim_history(self, history: str) -> str:
        if len(history) <= self.max_history_chars:
            return history

        return history[-self.max_history_chars:]


def _accepts_schema(method) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        or parameter.name == "schema"
        for parameter in parameters
    )


def _action_log_metadata(action: AgentAction) -> dict:
    metadata = {"action": action.action}
    if action.tool_call is not None:
        metadata["tool_name"] = action.tool_call.tool_name
        metadata["argument_names"] = sorted(action.tool_call.arguments)
        metadata["expected_capabilities"] = sorted(
            capability.value
            for capability in action.tool_call.expected_capabilities
        )
    if action.summary:
        metadata["summary_chars"] = len(action.summary)
    return metadata
