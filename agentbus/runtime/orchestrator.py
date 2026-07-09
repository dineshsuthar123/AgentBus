from dataclasses import dataclass
from typing import Any

from agentbus.agents.coder import CoderAgent
from agentbus.agents.planner import PlannerAgent
from agentbus.agents.reviewer import ReviewerAgent
from agentbus.config import AgentBusConfig
from agentbus.memory.run_log import RunLogger
from agentbus.runtime.verifier import Verifier
from agentbus.tools.filesystem import FileSystemTools
from agentbus.tools.git_tools import GitTools


@dataclass
class OrchestrationResult:
    plan: dict[str, Any]
    coder_summaries: list[str]
    verifier_result: dict[str, Any]
    reviewer_result: dict[str, Any]
    approved: bool
    retry_performed: bool
    final_summary: str

    @property
    def planner_summary(self) -> str:
        step_count = len(self.plan.get("steps", []))
        return f"{self.plan.get('goal', 'No goal')} ({step_count} steps)"


class MultiAgentOrchestrator:
    def __init__(
        self,
        config: AgentBusConfig | None = None,
        planner: PlannerAgent | None = None,
        coder: CoderAgent | None = None,
        reviewer: ReviewerAgent | None = None,
        verifier: Verifier | None = None,
        logger: RunLogger | None = None,
    ):
        self.config = config or AgentBusConfig.from_env()
        self.planner = planner or PlannerAgent(config=self.config)
        self.coder = coder or CoderAgent(config=self.config)
        self.reviewer = reviewer or ReviewerAgent(config=self.config)
        self.verifier = verifier or Verifier(config=self.config)
        self.logger = logger or RunLogger(log_dir=self.config.runs_dir)
        self.fs = FileSystemTools(workspace=self.config.workspace_dir)
        self.git = GitTools(workspace=self.config.workspace_dir)

    def run(self, user_task: str) -> OrchestrationResult:
        self.logger.log("run_started", {"workflow": "multi", "task": user_task})

        self.logger.log("planner_started", {})
        file_list = self.fs.list_files()
        plan = self.planner.plan(user_task, file_list=file_list)
        self.logger.log("planner_output", plan)

        coder_summaries = []
        self.logger.log("coder_started", {"attempt": 1})
        coder_summaries.append(self.coder.execute(user_task, plan))

        verifier_result = self.verifier.verify()
        self.logger.log("verifier_output", verifier_result)

        reviewer_result = self._review(user_task, plan, verifier_result)
        retry_performed = False

        if not reviewer_result["approved"]:
            retry_performed = True
            self.logger.log(
                "retry_started",
                {
                    "reason": "reviewer_rejected",
                    "required_fixes": reviewer_result.get("required_fixes", []),
                },
            )
            self.logger.log("coder_started", {"attempt": 2})
            coder_summaries.append(
                self.coder.execute(
                    user_task,
                    plan,
                    reviewer_feedback=reviewer_result,
                )
            )
            verifier_result = self.verifier.verify()
            self.logger.log("verifier_output", verifier_result)
            reviewer_result = self._review(user_task, plan, verifier_result)

        approved = bool(reviewer_result["approved"])
        final_summary = self._final_summary(approved, reviewer_result, verifier_result)
        result = OrchestrationResult(
            plan=plan,
            coder_summaries=coder_summaries,
            verifier_result=verifier_result,
            reviewer_result=reviewer_result,
            approved=approved,
            retry_performed=retry_performed,
            final_summary=final_summary,
        )

        self.logger.log(
            "orchestration_finished",
            {
                "approved": approved,
                "retry_performed": retry_performed,
                "summary": final_summary,
            },
        )
        self.logger.log("run_finished", {"summary": final_summary})
        return result

    def _review(
        self,
        user_task: str,
        plan: dict[str, Any],
        verifier_result: dict[str, Any],
    ) -> dict[str, Any]:
        git_diff = self.git.git_diff()
        reviewer_result = self.reviewer.review(
            user_task=user_task,
            plan=plan,
            git_diff=git_diff,
            test_output=verifier_result.get("output"),
        )
        self.logger.log(
            "reviewer_output",
            {
                "approved": reviewer_result["approved"],
                "issues": reviewer_result.get("issues", []),
                "summary": reviewer_result.get("summary", ""),
                "required_fixes": reviewer_result.get("required_fixes", []),
            },
        )
        return reviewer_result

    def _final_summary(
        self,
        approved: bool,
        reviewer_result: dict[str, Any],
        verifier_result: dict[str, Any],
    ) -> str:
        review_summary = reviewer_result.get("summary", "No reviewer summary.")
        verifier_status = "passed" if verifier_result.get("passed") else "failed"

        if approved:
            return f"Approved by reviewer. Verification {verifier_status}. {review_summary}"

        return f"Reviewer rejected after retry. Verification {verifier_status}. {review_summary}"
