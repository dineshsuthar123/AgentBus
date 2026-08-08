import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    unplanned_affected_components: list[str] = Field(
        default_factory=list,
        max_length=2_000,
    )
    missing_tests: list[str] = Field(default_factory=list, max_length=2_000)
    boundary_violations: list[str] = Field(default_factory=list, max_length=256)
    index_uncertainty: list[str] = Field(default_factory=list, max_length=256)


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
        relevant_changed_files: list[str] | None = None,
        generated_artifacts: list[str] | None = None,
        ignored_files: list[str] | None = None,
        tracked_generated_artifacts: list[str] | None = None,
        repository_intelligence: str | None = None,
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
  "required_fixes": ["..."],
  "unplanned_affected_components": ["..."],
  "missing_tests": ["..."],
  "boundary_violations": ["..."],
  "index_uncertainty": ["..."]
}}

Original user task:
{user_task}

Planner output:
{json.dumps(plan, indent=2)}

Git diff:
{git_diff}

Repository change classification:
{_artifact_note(relevant_changed_files, generated_artifacts, ignored_files, tracked_generated_artifacts)}

Repository intelligence review evidence:
{_intelligence_note(repository_intelligence)}

Test output:
{test_output or "No test output available."}

Compare actual changes with planned files and impacted components. Check likely
tests and architecture constraints against the diff and test output. Report
stale-index uncertainty. Intelligence candidates are heuristics, not proof, so
do not reject solely because a candidate is listed without corroborating evidence.
"""
        output = self.generate_json(prompt, schema=ReviewerOutput)
        return ReviewerOutput(**output).model_dump()

    def review_task(
        self,
        *,
        original_task: str,
        task_spec: dict,
        expected_outputs: list[str],
        artifacts: list[str],
        task_diff: str,
        coder_summary: str,
        verifier_result: dict,
        generated_artifacts: list[str] | None = None,
        ignored_files: list[str] | None = None,
        tracked_generated_artifacts: list[str] | None = None,
        repository_intelligence: str | None = None,
    ) -> dict:
        prompt = f"""
You are the AgentBus task-level Reviewer Agent.
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
  "required_fixes": ["..."],
  "unplanned_affected_components": ["..."],
  "missing_tests": ["..."],
  "boundary_violations": ["..."],
  "index_uncertainty": ["..."]
}}

Review only the current task against its own expected outputs and done criteria.
Do not reject it because downstream, dependent, or later tasks are incomplete.

Original task context:
{original_task}

Current TaskSpec:
{json.dumps(task_spec, indent=2)}

Expected outputs:
{json.dumps(expected_outputs, indent=2)}

Current task artifacts:
{json.dumps(artifacts, indent=2)}

Repository change classification:
{_artifact_note(artifacts, generated_artifacts, ignored_files, tracked_generated_artifacts)}

Current task diff and observations:
{task_diff}

Current task repository intelligence review evidence:
{_intelligence_note(repository_intelligence)}

Coder summary:
{coder_summary}

Current task verifier result:
{json.dumps(verifier_result, indent=2)}

Treat intelligence candidates as heuristics, not proof. Corroborate unplanned
effects, missing tests, and boundary violations with this task's diff and
verifier output, and report stale-index uncertainty explicitly.
"""
        output = self.generate_json(prompt, schema=ReviewerOutput)
        return ReviewerOutput(**output).model_dump()


def _artifact_note(
    relevant: list[str] | None,
    generated: list[str] | None,
    ignored: list[str] | None,
    tracked_generated: list[str] | None,
) -> str:
    relevant = list(relevant or [])[:50]
    generated = list(generated or [])[:50]
    ignored = list(ignored or [])[:50]
    tracked_generated = list(tracked_generated or [])[:50]
    excluded_generated = [
        path for path in generated if path not in set(tracked_generated)
    ]
    return json.dumps(
        {
            "relevant_changed_files": relevant,
            "generated_artifacts_detected": generated,
            "untracked_generated_artifacts_excluded_from_semantic_diff": (
                excluded_generated
            ),
            "git_ignored_files_excluded_from_semantic_diff": ignored,
            "tracked_generated_artifacts_kept_in_semantic_diff": tracked_generated,
            "review_policy": (
                "Do not reject solely because known untracked or ignored generated "
                "artifacts are listed separately and excluded from the proposed commit. "
                "Tracked generated artifacts remain reviewable and require scrutiny."
            ),
        },
        indent=2,
    )


def _intelligence_note(value: str | None) -> str:
    if not value:
        return "No repository intelligence evidence is available."
    return value[:12_000]
