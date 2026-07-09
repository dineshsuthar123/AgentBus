import json

from agentbus.agents.base import BaseAgent
from agentbus.config import AgentBusConfig
from agentbus.runtime.loop import AgentLoop


class CoderAgent(BaseAgent):
    def __init__(
        self,
        config: AgentBusConfig | None = None,
        model=None,
        loop_factory=AgentLoop,
    ):
        super().__init__(
            name="coder",
            role="Execute an approved local coding plan using AgentBus tools.",
            config=config,
            model=model,
        )
        self.loop_factory = loop_factory

    def execute(
        self,
        user_task: str,
        plan: dict,
        reviewer_feedback: dict | None = None,
    ) -> str:
        task = self._build_task(user_task, plan, reviewer_feedback)
        loop = self.loop_factory(config=self.config)
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
