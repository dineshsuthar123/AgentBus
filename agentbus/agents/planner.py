from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentbus.agents.base import BaseAgent
from agentbus.config import AgentBusConfig
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str
    risk: Literal["low", "medium", "high"]
    dependencies: list[str] | None = None
    assigned_role: str = "coder"
    maximum_attempts: int = Field(default=2, ge=1)
    expected_outputs: list[str] = Field(default_factory=list)
    done_criteria: list[str] | None = None


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    steps: list[PlanStep]
    test_strategy: str
    done_criteria: list[str]


class PlannerAgent(BaseAgent):
    def __init__(
        self,
        config: AgentBusConfig | None = None,
        model=None,
        model_router: ModelRouter | None = None,
    ):
        super().__init__(
            name="planner",
            role="Break a user task into a small, testable local implementation plan.",
            config=config,
            model=model,
            model_role=ModelRole.PLANNER,
            model_router=model_router,
        )

    def plan(
        self,
        user_task: str,
        file_list: str | None = None,
        context_pack: str | None = None,
    ) -> dict:
        context = context_pack or f"Current file list:\n{file_list or 'No file list available.'}"
        prompt = f"""
You are the AgentBus Planner Agent.
Return ONLY valid JSON with this shape:
{{
  "goal": "...",
  "steps": [
    {{
      "id": "step-1",
      "title": "...",
      "description": "...",
      "risk": "low|medium|high",
      "dependencies": ["optional-prerequisite-step-id"],
      "assigned_role": "coder",
      "maximum_attempts": 2,
      "expected_outputs": ["..."],
      "done_criteria": ["..."]
    }}
  ],
  "test_strategy": "...",
  "done_criteria": ["..."]
}}

User task:
{user_task}

Repo context:
{context}
"""
        output = self.generate_json(prompt, schema=PlannerOutput)
        return PlannerOutput(**output).model_dump(exclude_none=True)

    def summarize(self, plan: dict) -> str:
        step_count = len(plan.get("steps", []))
        return f"{plan.get('goal', 'No goal')} ({step_count} steps)"
