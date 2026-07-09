from agentbus.agents.planner import PlannerAgent
from agentbus.agents.reviewer import ReviewerAgent


class FakeModel:
    def __init__(self, output):
        self.output = output
        self.prompts = []

    def generate_json(self, prompt):
        self.prompts.append(prompt)
        return self.output


def test_planner_agent_parses_valid_model_output():
    model = FakeModel(
        {
            "goal": "Create calculator functions",
            "steps": [
                {
                    "id": "step-1",
                    "title": "Add module",
                    "description": "Create calculator.py",
                    "risk": "low",
                }
            ],
            "test_strategy": "Run pytest",
            "done_criteria": ["Tests pass"],
        }
    )
    planner = PlannerAgent(model=model)

    plan = planner.plan("create calculator", file_list="No files found.")

    assert plan["goal"] == "Create calculator functions"
    assert plan["steps"][0]["risk"] == "low"
    assert "create calculator" in model.prompts[0]


def test_reviewer_agent_parses_valid_model_output():
    model = FakeModel(
        {
            "approved": True,
            "issues": [],
            "summary": "Looks good",
            "required_fixes": [],
        }
    )
    reviewer = ReviewerAgent(model=model)

    review = reviewer.review(
        user_task="create calculator",
        plan={"goal": "calculator", "steps": []},
        git_diff="diff --git a/calculator.py b/calculator.py",
        test_output="1 passed",
    )

    assert review["approved"] is True
    assert review["summary"] == "Looks good"
    assert "1 passed" in model.prompts[0]
