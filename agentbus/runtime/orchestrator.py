from dataclasses import dataclass, field
from typing import Any

from agentbus.agents.coder import CoderAgent
from agentbus.agents.planner import PlannerAgent
from agentbus.agents.reviewer import ReviewerAgent
from agentbus.config import AgentBusConfig
from agentbus.git.branching import generate_branch_name
from agentbus.git.commit_message import generate_commit_message
from agentbus.git.repository import GitRepository, GitRepositoryError
from agentbus.github.pr import GitHubPullRequestClient
from agentbus.github.pr_body import build_pr_body
from agentbus.memory.run_log import RunLogger
from agentbus.repo.context_pack import ContextPackBuilder
from agentbus.repo.scanner import RepoScanner
from agentbus.repo.test_detection import TestCommandDetector
from agentbus.runtime.verifier import Verifier
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
    git_branch: str | None = None
    changed_files: list[str] = field(default_factory=list)
    commit_hash: str | None = None
    pr_url: str | None = None
    pr_error: str | None = None

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
        scanner: RepoScanner | None = None,
        test_detector: TestCommandDetector | None = None,
        context_builder: ContextPackBuilder | None = None,
        create_branch: bool = False,
        branch_name: str | None = None,
        commit_changes: bool = False,
        open_pr: bool = False,
        pr_base: str = "main",
        git_repository: GitRepository | None = None,
        pr_client: GitHubPullRequestClient | None = None,
        allow_existing_changes: bool = False,
    ):
        self.config = config or AgentBusConfig.from_env()
        self.planner = planner or PlannerAgent(config=self.config)
        self.coder = coder or CoderAgent(config=self.config)
        self.reviewer = reviewer or ReviewerAgent(config=self.config)
        self.verifier = verifier or Verifier(config=self.config)
        self.logger = logger or RunLogger(log_dir=self.config.runs_dir)
        self.scanner = scanner or RepoScanner(workspace=self.config.workspace_dir)
        self.test_detector = test_detector or TestCommandDetector(
            workspace=self.config.workspace_dir
        )
        self.context_builder = context_builder or ContextPackBuilder()
        self.git = GitTools(workspace=self.config.workspace_dir)
        self.git_repository = git_repository or GitRepository(
            workspace=self.config.workspace_dir
        )
        self.pr_client = pr_client or GitHubPullRequestClient(
            workspace=self.config.workspace_dir
        )
        self.create_branch = create_branch
        self.branch_name = branch_name
        self.commit_changes = commit_changes
        self.open_pr = open_pr
        self.pr_base = pr_base
        self.allow_existing_changes = allow_existing_changes

    def run(self, user_task: str) -> OrchestrationResult:
        self.logger.log("run_started", {"workflow": "multi", "task": user_task})

        git_branch = self._prepare_git_workflow(user_task)
        context_pack = self._build_context_pack(user_task)
        self.logger.log("planner_started", {})
        plan = self.planner.plan(user_task, context_pack=context_pack)
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
        changed_files, commit_hash, pr_url, pr_error = self._finalize_git_workflow(
            user_task=user_task,
            plan=plan,
            verifier_result=verifier_result,
            reviewer_result=reviewer_result,
            approved=approved,
            git_branch=git_branch,
        )
        final_summary = self._final_summary(approved, reviewer_result, verifier_result)
        result = OrchestrationResult(
            plan=plan,
            coder_summaries=coder_summaries,
            verifier_result=verifier_result,
            reviewer_result=reviewer_result,
            approved=approved,
            retry_performed=retry_performed,
            final_summary=final_summary,
            git_branch=git_branch,
            changed_files=changed_files,
            commit_hash=commit_hash,
            pr_url=pr_url,
            pr_error=pr_error,
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

    def _git_workflow_requested(self) -> bool:
        return self.create_branch or self.commit_changes or self.open_pr

    def _prepare_git_workflow(self, user_task: str) -> str | None:
        if not self._git_workflow_requested():
            return None

        is_repo = self.git_repository.is_git_repo()
        current_branch = self.git_repository.current_branch() if is_repo else None
        self.logger.log(
            "git_repo_checked",
            {
                "is_git_repo": is_repo,
                "current_branch": current_branch,
            },
        )

        if not is_repo:
            raise GitRepositoryError("Workspace is not a git repository.")

        if not self.create_branch:
            return current_branch

        if (
            self.git_repository.has_uncommitted_changes()
            and not self.allow_existing_changes
        ):
            raise GitRepositoryError(
                "Cannot create branch with existing uncommitted changes."
            )

        branch = self.branch_name or generate_branch_name(user_task)
        message = self.git_repository.create_branch(branch)
        self.logger.log(
            "branch_created",
            {
                "branch": branch,
                "message": message,
            },
        )
        return branch

    def _finalize_git_workflow(
        self,
        *,
        user_task: str,
        plan: dict[str, Any],
        verifier_result: dict[str, Any],
        reviewer_result: dict[str, Any],
        approved: bool,
        git_branch: str | None,
    ) -> tuple[list[str], str | None, str | None, str | None]:
        if not self._git_workflow_requested():
            return [], None, None, None

        changed_files = self.git_repository.changed_files()
        self.logger.log("changed_files_detected", {"files": changed_files})

        if not approved or not verifier_result.get("passed"):
            return changed_files, None, None, None

        commit_hash = None
        if self.commit_changes:
            message = generate_commit_message(user_task, changed_files)
            commit_hash = self.git_repository.commit(message)
            self.logger.log(
                "commit_created",
                {
                    "commit_hash": commit_hash,
                    "message": message,
                },
            )

        if not self.open_pr:
            return changed_files, commit_hash, None, None

        if not self.commit_changes or not commit_hash:
            error = "PR creation requires a successful commit."
            self.logger.log("pr_creation_failed", {"error": error})
            return changed_files, commit_hash, None, error

        branch = git_branch or self.git_repository.current_branch()
        title = generate_commit_message(user_task, changed_files)
        body = build_pr_body(
            user_task=user_task,
            planner_summary=self._planner_summary(plan),
            verifier_result=verifier_result,
            reviewer_result=reviewer_result,
            changed_files=changed_files,
            test_command=verifier_result.get("command"),
        )

        self.logger.log("pr_creation_started", {"base": self.pr_base, "head": branch})

        try:
            self.git_repository.push_branch(branch)
            pr_result = self.pr_client.create_pr(
                title=title,
                body=body,
                base=self.pr_base,
                head=branch,
            )
        except Exception as exc:
            error = str(exc)
            self.logger.log("pr_creation_failed", {"error": error})
            return changed_files, commit_hash, None, error

        if pr_result.startswith("http"):
            self.logger.log("pr_created", {"url": pr_result})
            return changed_files, commit_hash, pr_result, None

        self.logger.log("pr_creation_failed", {"error": pr_result})
        return changed_files, commit_hash, None, pr_result

    def _build_context_pack(self, user_task: str) -> str:
        self.logger.log("repo_scan_started", {})
        scan_result = self.scanner.scan()
        self.logger.log(
            "repo_scan_finished",
            {
                "files": len(scan_result.get("files", [])),
                "directories": len(scan_result.get("directories", [])),
                "detected_languages": scan_result.get("detected_languages", []),
                "detected_frameworks": scan_result.get("detected_frameworks", []),
            },
        )

        test_detection = self.test_detector.detect()
        self.logger.log("test_command_detected", test_detection)

        context_pack = self.context_builder.build(
            scan_result,
            test_detection,
            user_task=user_task,
        )
        self.logger.log(
            "context_pack_created",
            {
                "chars": len(context_pack),
            },
        )
        return context_pack

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

    def _planner_summary(self, plan: dict[str, Any]) -> str:
        step_count = len(plan.get("steps", []))
        return f"{plan.get('goal', 'No goal')} ({step_count} steps)"
