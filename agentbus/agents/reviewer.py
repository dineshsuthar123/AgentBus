import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agentbus.agents.base import BaseAgent
from agentbus.config import AgentBusConfig
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["low", "medium", "high"]
    message: str
    file: str | None = None


class ReviewerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    issues: list[ReviewIssue]
    summary: str
    required_fixes: list[str]


class ReviewerAgent(BaseAgent):
    def __init__(
        self,
        config: AgentBusConfig | None = None,
        model=None,
        model_router: ModelRouter | None = None,
    ):
        super().__init__(
            name="reviewer",
            role="Review local changes against the task, plan, diff, and tests.",
            config=config,
            model=model,
            model_role=ModelRole.REVIEWER,
            model_router=model_router,
        )

    def review(
        self,
        user_task: str,
        plan: dict,
        git_diff: str,
        test_output: str | None = None,
    ) -> dict:
        prompt = f"""
You are the AgentBus Reviewer Agent.
Return ONLY valid JSON with this shape:
{{
  "approved": true,
  "issues": [
    {{
      "severity": "low|medium|high",
      "message": "...",
      "file": "optional/path"
    }}
  ],
  "summary": "...",
  "required_fixes": ["..."]
}}

Original user task:
{user_task}

Planner output:
{json.dumps(plan, indent=2)}

Git diff:
{git_diff}

Test output:
{test_output or "No test output available."}
"""
        output = self.generate_json(prompt, schema=ReviewerOutput)
        return ReviewerOutput(**output).model_dump()
