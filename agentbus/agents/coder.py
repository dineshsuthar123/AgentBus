import inspect
import json

from agentbus.agents.base import BaseAgent
from agentbus.config import AgentBusConfig
from agentbus.execution.cancellation import CancellationToken
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.state_store import StateStore
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole
from agentbus.runtime.loop import AgentLoop
from agentbus.tools.protocol import ToolResourceBudget
from agentbus.tools.runtime import ManagedToolRuntime


class CoderAgent(BaseAgent):
    def __init__(
        self,
        config: AgentBusConfig | None = None,
        model=None,
        loop_factory=AgentLoop,
        model_router: ModelRouter | None = None,
    ):
        super().__init__(
            name="coder",
            role="Execute an approved local coding plan using AgentBus tools.",
            config=config,
            model=model,
            model_role=ModelRole.CODER,
            model_router=model_router,
        )
        self.loop_factory = loop_factory

    def execute(
        self,
        user_task: str,
        plan: dict,
        reviewer_feedback: dict | None = None,
        cancellation: CancellationToken | None = None,
        tool_runtime: ManagedToolRuntime | None = None,
        state_store: StateStore | None = None,
        cancellation_registry: CancellationRegistry | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        workspace_trusted: bool = True,
        provider_consented: bool = True,
        resource_budget: ToolResourceBudget | None = None,
        policy_context: dict | None = None,
    ) -> str:
        task = self._build_task(user_task, plan, reviewer_feedback)
        loop_arguments = {"config": self.config}
        if _accepts_keyword(self.loop_factory, "model"):
            loop_arguments["model"] = self.model
        if _accepts_keyword(self.loop_factory, "cancellation"):
            loop_arguments["cancellation"] = cancellation
        optional_arguments = {
            "tool_runtime": tool_runtime,
            "state_store": state_store,
            "cancellation_registry": cancellation_registry,
            "run_id": run_id,
            "task_id": task_id,
            "workspace_trusted": workspace_trusted,
            "provider_consented": provider_consented,
            "resource_budget": resource_budget,
            "policy_context": policy_context,
        }
        for name, value in optional_arguments.items():
            if _accepts_keyword(self.loop_factory, name):
                loop_arguments[name] = value
        loop = self.loop_factory(**loop_arguments)
        return loop.run(task)

    def _build_task(
        self,
        user_task: str,
        plan: dict,
        reviewer_feedback: dict | None,
    ) -> str:
        feedback = ""
        if reviewer_feedback:
            feedback = (
                "\nReviewer required fixes:\n"
                f"{json.dumps(reviewer_feedback.get('required_fixes', []), indent=2)}\n"
                "Reviewer issues:\n"
                f"{json.dumps(reviewer_feedback.get('issues', []), indent=2)}\n"
            )

        return f"""
Execute this AgentBus plan locally.

Original user task:
{user_task}

Plan:
{json.dumps(plan, indent=2)}
{feedback}
Use the existing tools, run verification where practical, inspect git diff before finishing, and finish with a concise summary.
"""


def _accepts_keyword(factory, keyword: str) -> bool:
    try:
        parameters = inspect.signature(factory).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        or parameter.name == keyword
        for parameter in parameters
    )
