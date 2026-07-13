import inspect
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentbus.agents.coder import CoderAgent
from agentbus.agents.planner import PlannerAgent
from agentbus.agents.reviewer import ReviewerAgent
from agentbus.config import AgentBusConfig
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.integration import IntegrationCoordinator
from agentbus.execution.leases import LeaseService
from agentbus.execution.models import ExecutionReport, RunStatus
from agentbus.execution.scheduler import ParallelExecutionScheduler
from agentbus.execution.state_store import StateStore, StateStoreError
from agentbus.execution.worker import LocalTaskWorker
from agentbus.git.branching import generate_branch_name
from agentbus.git.commit_message import generate_commit_message
from agentbus.git.repository import (
    GitRepository,
    GitRepositoryError,
    RepositoryChangeSet,
    WorkspaceRepositoryMismatch,
)
from agentbus.github.pr import GitHubPullRequestClient
from agentbus.github.pr_body import build_pr_body
from agentbus.memory.run_log import RunLogger
from agentbus.models.router import (
    ModelProviderFactory,
    ModelRouter,
    model_request_context,
)
from agentbus.repo.context_pack import ContextPackBuilder
from agentbus.repo.scanner import RepoScanner
from agentbus.repo.test_detection import TestCommandDetector
from agentbus.runtime.verifier import Verifier
from agentbus.runtime.durable_workflow import MultiAgentTaskExecutor
from agentbus.tools.git_tools import GitTools
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.errors import WorktreeError
from agentbus.worktrees.models import WorktreePurpose


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
        git_tools: GitTools | None = None,
        pr_client: GitHubPullRequestClient | None = None,
        allow_existing_changes: bool = False,
        state_store: StateStore | None = None,
        durable_crash_hook=None,
        model_router: ModelRouter | None = None,
        parallel_executor_factory=None,
        parallel_final_verifier=None,
        parallel_final_reviewer=None,
        worker_crash_hook=None,
    ):
        self.config = config or AgentBusConfig.from_env()
        self.workspace = self.config.workspace_path
        self.logger = logger or RunLogger(log_dir=self.config.runs_dir)
        self.model_router = model_router or ModelRouter(
            self.config,
            logger=self.logger,
        )
        self.model_router.set_logger(self.logger)
        self.planner = planner or PlannerAgent(
            config=self.config,
            model_router=self.model_router,
        )
        self.coder = coder or CoderAgent(
            config=self.config,
            model_router=self.model_router,
        )
        self.reviewer = reviewer or ReviewerAgent(
            config=self.config,
            model_router=self.model_router,
        )
        self.verifier = verifier or Verifier(config=self.config)
        self.scanner = scanner or RepoScanner(workspace=str(self.workspace))
        self.test_detector = test_detector or TestCommandDetector(
            workspace=str(self.workspace)
        )
        self.context_builder = context_builder or ContextPackBuilder(
            workspace=str(self.workspace)
        )
        self.git = git_tools or GitTools(workspace=str(self.workspace))
        self.git_repository = git_repository or GitRepository(
            workspace=str(self.workspace)
        )
        self.pr_client = pr_client or GitHubPullRequestClient(
            workspace=str(self.workspace)
        )
        self.create_branch = create_branch
        self.branch_name = branch_name
        self.commit_changes = commit_changes
        self.open_pr = open_pr
        self.pr_base = pr_base
        self.allow_existing_changes = allow_existing_changes
        self._state_store = state_store
        self.durable_crash_hook = durable_crash_hook
        self.parallel_executor_factory = parallel_executor_factory
        self.parallel_final_verifier = parallel_final_verifier
        self.parallel_final_reviewer = parallel_final_reviewer
        self.worker_crash_hook = worker_crash_hook

    def run(self, user_task: str) -> OrchestrationResult:
        self.logger.log(
            "run_started",
            {
                "workflow": "multi",
                "task_chars": len(user_task),
            },
        )

        git_branch = self._prepare_git_workflow(user_task)
        context_pack = self._build_context_pack(user_task)
        self.logger.log("planner_started", {})
        with model_request_context(run_id=self.logger.run_id):
            plan = self.planner.plan(user_task, context_pack=context_pack)
        self.logger.log("planner_output", _plan_log_metadata(plan))

        coder_summaries = []
        self.logger.log("coder_started", {"attempt": 1})
        with model_request_context(run_id=self.logger.run_id):
            coder_summaries.append(self.coder.execute(user_task, plan))

        verifier_result = self.verifier.verify()
        self.logger.log("verifier_output", _verifier_log_metadata(verifier_result))

        reviewer_result = self._review(user_task, plan, verifier_result)
        retry_performed = False

        if not reviewer_result["approved"]:
            retry_performed = True
            self.logger.log(
                "retry_started",
                {
                    "reason": "reviewer_rejected",
                    "required_fix_count": len(
                        reviewer_result.get("required_fixes", [])
                    ),
                },
            )
            self.logger.log("coder_started", {"attempt": 2})
            with model_request_context(run_id=self.logger.run_id):
                coder_summaries.append(
                    self.coder.execute(
                        user_task,
                        plan,
                        reviewer_feedback=reviewer_result,
                    )
                )
            verifier_result = self.verifier.verify()
            self.logger.log(
                "verifier_output",
                _verifier_log_metadata(verifier_result),
            )
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
                "summary_chars": len(final_summary),
            },
        )
        self.logger.log("run_finished", {"summary_chars": len(final_summary)})
        return result

    @property
    def state_store(self) -> StateStore:
        if self._state_store is None:
            self._state_store = StateStore(self.config.state_database_path)
        return self._state_store

    def create_durable_run(self, user_task: str) -> str:
        """Plan and persist an opt-in durable run without executing a task."""
        run_id = uuid.uuid4().hex
        self.logger = RunLogger(log_dir=self.config.runs_dir, run_id=run_id)
        self.model_router.set_logger(self.logger)
        self.logger.log(
            "run_started",
            {
                "workflow": "multi",
                "durable": True,
                "task_chars": len(user_task),
            },
        )
        git_top_level = self._validate_workspace_repository()
        parallel_enabled = bool(self.config.parallel_execution)
        base_commit = (
            self.git_repository.head_commit(short=False) if parallel_enabled else None
        )
        snapshot = getattr(self.git_repository, "worktree_snapshot", None)
        workspace_baseline = snapshot() if snapshot is not None else {}
        git_branch = None if parallel_enabled else self._prepare_git_workflow(user_task)
        initial_head = None
        if self._git_workflow_requested() and self.git_repository.is_git_repo():
            initial_head = self.git_repository.head_commit()

        context_pack = self._build_context_pack(user_task)
        self.logger.log("planner_started", {})
        with model_request_context(run_id=run_id):
            plan = self.planner.plan(user_task, context_pack=context_pack)
        self.logger.log("planner_output", _plan_log_metadata(plan))
        metadata = {
            "git": {
                "requested": self._git_workflow_requested(),
                "create_branch": self.create_branch,
                "commit_changes": self.commit_changes,
                "open_pr": self.open_pr,
                "pr_base": self.pr_base,
                "branch": git_branch,
                "branch_name": self.branch_name,
                "initial_head": initial_head,
            },
            "model_routing": self.config.safe_model_summary(),
            "planner_model_result": _last_model_result(self.planner),
            "workspace_repository": {
                "workspace": str(self.workspace),
                "git_top_level": str(git_top_level),
            },
            "final_review": {"required": True, "status": "pending"},
            "workspace_baseline": workspace_baseline,
            "parallel_execution": {
                "enabled": parallel_enabled,
                "max_workers": self.config.max_workers,
                "lease_seconds": self.config.worker_lease_seconds,
                "heartbeat_seconds": self.config.worker_heartbeat_seconds,
                "worktree_root": str(self.config.worktree_root_path),
                "keep_worktrees": self.config.keep_worktrees,
                "integration_strategy": self.config.integration_strategy,
                "base_commit": base_commit,
                "workers_used": [],
                "integration_order": [],
            },
        }
        engine = self._durable_engine(run_id)
        engine.create_run(
            user_task,
            plan,
            workflow_type="multi",
            model=self.config.resolve_model("coder"),
            workspace=str(self.workspace),
            context_summary=context_pack,
            metadata=metadata,
            run_id=run_id,
        )
        return run_id

    def run_durable(self, run_id: str, *, resume: bool = False) -> ExecutionReport:
        """Execute or resume a persisted multi-agent run and finalize Git safely."""
        persisted = self.state_store.get_run(run_id)
        parallel = persisted.metadata.get("parallel_execution", {})
        if isinstance(parallel, dict) and parallel.get("enabled"):
            scheduler = self._parallel_scheduler(run_id)
            report = scheduler.run(run_id, resume=resume)
            if report.status == RunStatus.WAITING_FOR_REVIEW:
                report = self._run_parallel_final_review(run_id)
            if report.status == RunStatus.SUCCEEDED:
                report = self._finalize_parallel_git(run_id)
            return report
        engine = self._durable_engine(run_id)
        report = engine.resume(run_id) if resume else engine.run_until_blocked(run_id)
        if report.status == RunStatus.WAITING_FOR_REVIEW:
            report = self._run_final_review(run_id)
        if report.status == RunStatus.SUCCEEDED:
            report = self._finalize_durable_git(run_id)
        return report

    def resume_durable(self, run_id: str) -> ExecutionReport:
        return self.run_durable(run_id, resume=True)

    def get_durable_report(self, run_id: str) -> ExecutionReport:
        return self._durable_engine(run_id, executor=False).get_report(run_id)

    def _durable_engine(
        self,
        run_id: str,
        *,
        executor: bool = True,
    ) -> DurableExecutionEngine:
        logger = RunLogger(log_dir=self.config.runs_dir, run_id=run_id)
        task_executor = None
        if executor:
            task_executor = MultiAgentTaskExecutor(
                coder=self.coder,
                verifier=self.verifier,
                reviewer=self.reviewer,
                git_tools=self.git,
                git_repository=self.git_repository,
                workspace=str(self.workspace),
            )
        return DurableExecutionEngine(
            self.state_store,
            task_executor,
            logger=logger,
            crash_hook=self.durable_crash_hook,
        )

    def _parallel_scheduler(self, run_id: str) -> ParallelExecutionScheduler:
        run = self.state_store.get_run(run_id)
        parallel = run.metadata.get("parallel_execution", {})
        if not isinstance(parallel, dict):
            raise GitRepositoryError("Persisted parallel execution metadata is invalid.")
        manager = GitWorktreeManager(
            self.workspace,
            parallel.get("worktree_root") or self.config.worktree_root_path,
            self.state_store,
        )
        leases = LeaseService(
            self.state_store,
            lease_seconds=float(
                parallel.get("lease_seconds", self.config.worker_lease_seconds)
            ),
        )
        integration = IntegrationCoordinator(self.state_store, manager)

        def worker_factory(worker_id: str) -> LocalTaskWorker:
            return LocalTaskWorker(
                worker_id=worker_id,
                store=self.state_store,
                lease_service=leases,
                worktree_manager=manager,
                executor_factory=self._parallel_task_executor,
                heartbeat_seconds=float(
                    parallel.get(
                        "heartbeat_seconds", self.config.worker_heartbeat_seconds
                    )
                ),
                crash_hook=self.worker_crash_hook,
            )

        return ParallelExecutionScheduler(
            store=self.state_store,
            worktree_manager=manager,
            lease_service=leases,
            integration=integration,
            worker_factory=worker_factory,
            max_workers=int(parallel.get("max_workers", 1)),
        )

    def _parallel_task_executor(self, workspace: Path):
        if self.parallel_executor_factory is not None:
            return self.parallel_executor_factory(workspace)
        config = self.config.with_overrides(
            workspace_dir=str(workspace), parallel_execution=False
        )
        logger = RunLogger(log_dir=config.runs_dir)
        factory = ModelProviderFactory(
            config,
            builders=dict(self.model_router.provider_factory.builders),
        )
        router = ModelRouter(
            config,
            provider_factory=factory,
            logger=logger,
            usage_ledger=self.model_router.usage_ledger,
            sleeper=self.model_router.sleeper,
            jitter=self.model_router.jitter,
        )
        repository = GitRepository(str(workspace))
        return MultiAgentTaskExecutor(
            coder=CoderAgent(config=config, model_router=router),
            verifier=Verifier(config=config),
            reviewer=ReviewerAgent(config=config, model_router=router),
            git_tools=GitTools(str(workspace)),
            git_repository=repository,
            workspace=str(workspace),
        )

    def _run_final_review(self, run_id: str) -> ExecutionReport:
        run = self.state_store.get_run(run_id)
        if run.status != RunStatus.WAITING_FOR_REVIEW:
            return self.get_durable_report(run_id)

        persisted_review = run.metadata.get("final_review", {})
        if isinstance(persisted_review, dict):
            persisted_status = persisted_review.get("status")
            if persisted_status in {
                "approved",
                "rejected",
                "blocked_by_verification",
            }:
                approved = persisted_status == "approved"
                self.state_store.update_run_status(
                    run_id,
                    RunStatus.SUCCEEDED if approved else RunStatus.FAILED,
                    failure_reason=(
                        None
                        if approved
                        else (
                            "Final verification failed."
                            if persisted_status == "blocked_by_verification"
                            else "Final reviewer rejected the run."
                        )
                    ),
                    event_type="final_review_state_reconciled",
                )
                return self.get_durable_report(run_id)

        verifier_result = self._verify_final()
        baseline = run.metadata.get("workspace_baseline", {})
        changed_since = getattr(self.git_repository, "changed_since", None)
        changed_files = (
            changed_since(baseline)
            if changed_since is not None and isinstance(baseline, dict)
            else run.changed_files or self.git_repository.changed_files()
        )
        changes = self._repository_changes(changed_files)
        verifier_status = "passed" if verifier_result.get("passed") else "failed"
        if not verifier_result.get("passed"):
            self.state_store.update_run_details(
                run_id,
                verifier_status=verifier_status,
                reviewer_status="not_run",
                changed_files=changed_files,
                metadata_updates={
                    "artifact_hygiene": changes.to_metadata(),
                    "verifier_artifact_suppression_active": bool(
                        verifier_result.get("artifact_suppression_active")
                    ),
                    "final_review": {
                        "required": True,
                        "status": "blocked_by_verification",
                        "summary": "Final verification failed before review.",
                        "issues": [],
                        "required_fixes": [
                            verifier_result.get("reason")
                            or "Make the final verification command pass."
                        ],
                    }
                },
                event_type="final_verification_failed",
            )
            self.state_store.update_run_status(
                run_id,
                RunStatus.FAILED,
                failure_reason="Final verification failed.",
                event_type="durable_run_final_verification_failed",
            )
            return self.get_durable_report(run_id)

        review_diff = getattr(self.git_repository, "review_diff", None)
        git_diff = (
            review_diff(max_chars=30_000, paths=changes.changed_files)
            if review_diff is not None
            else self._fallback_review_diff(changes)
        )
        with model_request_context(run_id=run_id):
            reviewer_result = self._call_reviewer(
                user_task=run.original_task,
                plan=run.planner_output,
                git_diff=git_diff,
                test_output=verifier_result.get("output"),
                changes=changes,
            )
        approved = bool(reviewer_result.get("approved"))
        self.state_store.update_run_details(
            run_id,
            verifier_status=verifier_status,
            reviewer_status="approved" if approved else "rejected",
            changed_files=changed_files,
            metadata_updates={
                "artifact_hygiene": changes.to_metadata(),
                "verifier_artifact_suppression_active": bool(
                    verifier_result.get("artifact_suppression_active")
                ),
                "final_review": {
                    "required": True,
                    "status": "approved" if approved else "rejected",
                    "summary": reviewer_result.get("summary", ""),
                    "issues": reviewer_result.get("issues", []),
                    "required_fixes": reviewer_result.get("required_fixes", []),
                },
                "final_reviewer_model_result": _last_model_result(self.reviewer),
            },
            event_type="final_review_completed",
        )
        self.state_store.update_run_status(
            run_id,
            RunStatus.SUCCEEDED if approved else RunStatus.FAILED,
            failure_reason=None if approved else "Final reviewer rejected the run.",
            event_type=(
                "durable_run_succeeded"
                if approved
                else "durable_run_final_review_rejected"
            ),
        )
        return self.get_durable_report(run_id)

    def _run_parallel_final_review(self, run_id: str) -> ExecutionReport:
        run = self.state_store.get_run(run_id)
        if run.status != RunStatus.WAITING_FOR_REVIEW:
            return self.get_durable_report(run_id)
        persisted_review = run.metadata.get("final_review", {})
        if isinstance(persisted_review, dict) and persisted_review.get("status") in {
            "approved",
            "rejected",
            "blocked_by_verification",
        }:
            approved = persisted_review.get("status") == "approved"
            self.state_store.update_run_status(
                run_id,
                RunStatus.SUCCEEDED if approved else RunStatus.FAILED,
                failure_reason=None if approved else "Parallel final acceptance failed.",
                event_type="final_review_state_reconciled",
            )
            return self.get_durable_report(run_id)
        parallel = run.metadata.get("parallel_execution", {})
        if not isinstance(parallel, dict):
            raise GitRepositoryError("Persisted parallel execution metadata is invalid.")
        worktree_id = parallel.get("integration_worktree_id")
        base_commit = parallel.get("base_commit")
        if not isinstance(worktree_id, str) or not worktree_id:
            raise GitRepositoryError(
                "Parallel run is missing its persisted integration worktree ID."
            )
        if not isinstance(base_commit, str) or not base_commit:
            raise GitRepositoryError("Parallel run is missing its exact base commit.")
        try:
            manager = GitWorktreeManager(
                self.workspace,
                parallel.get("worktree_root") or self.config.worktree_root_path,
                self.state_store,
            )
            integration_worktree = self.state_store.get_worktree(worktree_id)
            if (
                integration_worktree.run_id != run_id
                or integration_worktree.purpose != WorktreePurpose.INTEGRATION
            ):
                raise GitRepositoryError(
                    "Persisted integration worktree ownership does not match the run."
                )
            integration_path = manager.validate(integration_worktree)
        except (StateStoreError, WorktreeError) as exc:
            raise GitRepositoryError(
                f"Unable to validate the persisted integration worktree: {exc}"
            ) from exc
        repository = GitRepository(str(integration_path))
        repository.validate_workspace()
        verifier, reviewer = self._parallel_final_runtime(integration_path)
        self.state_store.record_event(
            run_id,
            "final_integration_verification_started",
            {"integration_worktree": str(integration_path)},
        )
        verify = verifier.verify
        verifier_result = (
            verify(require_command=True)
            if "require_command" in inspect.signature(verify).parameters
            else verify()
        )
        changed_files = repository.changed_files_between(base_commit)
        changes = repository.change_set(changed_files)
        integration_commit = repository.head_commit(short=False)
        parallel = {**parallel, "integration_commit": integration_commit}
        if not verifier_result.get("passed"):
            self.state_store.update_run_details(
                run_id,
                verifier_status="failed",
                reviewer_status="not_run",
                changed_files=changed_files,
                metadata_updates={
                    "parallel_execution": parallel,
                    "artifact_hygiene": changes.to_metadata(),
                    "verifier_artifact_suppression_active": bool(
                        verifier_result.get("artifact_suppression_active")
                    ),
                    "final_review": {
                        "required": True,
                        "status": "blocked_by_verification",
                        "summary": "Integrated final verification failed.",
                        "issues": [],
                        "required_fixes": [
                            verifier_result.get("reason")
                            or "Make integrated verification pass."
                        ],
                    },
                },
                event_type="final_integration_verification_failed",
            )
            self.state_store.update_run_status(
                run_id,
                RunStatus.FAILED,
                failure_reason="Integrated final verification failed.",
                event_type="durable_run_final_verification_failed",
            )
            return self.get_durable_report(run_id)
        git_diff = repository.commit_diff(base_commit, max_chars=30_000)
        with model_request_context(run_id=run_id):
            reviewer_result = self._call_reviewer(
                user_task=run.original_task,
                plan=run.planner_output,
                git_diff=git_diff,
                test_output=verifier_result.get("output"),
                changes=changes,
                reviewer=reviewer,
            )
        approved = bool(reviewer_result.get("approved"))
        self.state_store.update_run_details(
            run_id,
            verifier_status="passed",
            reviewer_status="approved" if approved else "rejected",
            changed_files=changed_files,
            metadata_updates={
                "parallel_execution": parallel,
                "artifact_hygiene": changes.to_metadata(),
                "verifier_artifact_suppression_active": bool(
                    verifier_result.get("artifact_suppression_active")
                ),
                "final_review": {
                    "required": True,
                    "status": "approved" if approved else "rejected",
                    "summary": reviewer_result.get("summary", ""),
                    "issues": reviewer_result.get("issues", []),
                    "required_fixes": reviewer_result.get("required_fixes", []),
                },
            },
            event_type="final_integration_review_completed",
        )
        self.state_store.update_run_status(
            run_id,
            RunStatus.SUCCEEDED if approved else RunStatus.FAILED,
            failure_reason=None if approved else "Final reviewer rejected integrated work.",
            event_type=(
                "durable_run_succeeded"
                if approved
                else "durable_run_final_review_rejected"
            ),
        )
        return self.get_durable_report(run_id)

    def _parallel_final_runtime(self, workspace: Path):
        verifier = self.parallel_final_verifier
        reviewer = self.parallel_final_reviewer
        if verifier is not None and reviewer is not None:
            return verifier, reviewer
        config = self.config.with_overrides(
            workspace_dir=str(workspace), parallel_execution=False
        )
        factory = ModelProviderFactory(
            config, builders=dict(self.model_router.provider_factory.builders)
        )
        router = ModelRouter(
            config,
            provider_factory=factory,
            logger=RunLogger(log_dir=config.runs_dir),
            usage_ledger=self.model_router.usage_ledger,
            sleeper=self.model_router.sleeper,
            jitter=self.model_router.jitter,
        )
        return (
            verifier or Verifier(config=config),
            reviewer or ReviewerAgent(config=config, model_router=router),
        )

    def _finalize_parallel_git(self, run_id: str) -> ExecutionReport:
        run = self.state_store.get_run(run_id)
        git_options = run.metadata.get("git", {})
        parallel = run.metadata.get("parallel_execution", {})
        if not isinstance(git_options, dict) or not isinstance(parallel, dict):
            return self.get_durable_report(run_id)
        if not git_options.get("requested"):
            return self.get_durable_report(run_id)
        if run.verifier_status != "passed" or run.reviewer_status != "approved":
            return self.get_durable_report(run_id)
        integration_commit = parallel.get("integration_commit")
        if not isinstance(integration_commit, str) or not integration_commit:
            self.state_store.update_run_details(
                run_id,
                finalization_error="Parallel run has no persisted integration commit.",
                event_type="git_finalization_blocked",
            )
            return self.get_durable_report(run_id)
        branch = parallel.get("final_branch")
        if git_options.get("create_branch") and not branch:
            branch = git_options.get("branch_name") or generate_branch_name(
                run.original_task
            )
            existing_commit = self.git_repository.branch_commit(branch)
            if existing_commit is not None and existing_commit != integration_commit:
                self.state_store.update_run_details(
                    run_id,
                    finalization_error=(
                        f"Branch '{branch}' already exists at an unrelated commit."
                    ),
                    event_type="branch_creation_failed",
                )
                return self.get_durable_report(run_id)
            if existing_commit is None:
                try:
                    self.git_repository.create_branch_at(branch, integration_commit)
                except GitRepositoryError as exc:
                    self.state_store.update_run_details(
                        run_id,
                        finalization_error=str(exc),
                        event_type="branch_creation_failed",
                    )
                    return self.get_durable_report(run_id)
            parallel = {**parallel, "final_branch": branch}
            self.state_store.update_run_details(
                run_id,
                metadata_updates={"parallel_execution": parallel},
                event_type="branch_created",
            )
        if git_options.get("commit_changes") and not run.commit_identifier:
            self.state_store.update_run_details(
                run_id,
                commit_identifier=integration_commit,
                event_type="integration_commit_published",
            )
        run = self.state_store.get_run(run_id)
        if not git_options.get("open_pr") or run.pr_url:
            return self.get_durable_report(run_id)
        if self._has_unresolved_pr_creation(run_id):
            return self.get_durable_report(run_id)
        if not branch:
            self.state_store.update_run_details(
                run_id,
                finalization_error=(
                    "Parallel PR creation requires --create-branch so the verified "
                    "integration commit has an explicit user-facing ref."
                ),
                event_type="pr_creation_failed",
            )
            return self.get_durable_report(run_id)
        title = generate_commit_message(run.original_task, run.changed_files)
        body = build_pr_body(
            user_task=run.original_task,
            planner_summary=self._planner_summary(run.planner_output),
            verifier_result={"passed": True, "command": [], "reason": "Integrated run"},
            reviewer_result={"approved": True, "summary": "Integrated run approved"},
            changed_files=run.changed_files,
            test_command=None,
        )
        try:
            self.state_store.record_event(
                run_id,
                "pr_creation_started",
                {"base": git_options.get("pr_base", "main"), "head": branch},
            )
            self.git_repository.push_branch(branch)
            pr_result = self.pr_client.create_pr(
                title=title,
                body=body,
                base=git_options.get("pr_base", "main"),
                head=branch,
            )
        except Exception as exc:
            self.state_store.update_run_details(
                run_id,
                finalization_error=str(exc),
                event_type="pr_creation_outcome_unknown",
            )
            return self.get_durable_report(run_id)
        if pr_result.startswith("http"):
            self.state_store.update_run_details(
                run_id,
                pr_url=pr_result,
                event_type="pr_created",
                clear_finalization_error=True,
            )
        else:
            self.state_store.update_run_details(
                run_id, finalization_error=pr_result, event_type="pr_creation_failed"
            )
        return self.get_durable_report(run_id)

    def _verify_final(self) -> dict[str, Any]:
        verify = self.verifier.verify
        if "require_command" in inspect.signature(verify).parameters:
            return verify(require_command=True)
        return verify()

    def _validate_workspace_repository(self):
        validate = getattr(self.git_repository, "validate_workspace", None)
        if validate is not None:
            return validate()
        if not self.git_repository.is_git_repo():
            raise GitRepositoryError("Workspace is not a git repository.")
        return self.workspace

    def _finalize_durable_git(self, run_id: str) -> ExecutionReport:
        run = self.state_store.get_run(run_id)
        git_options = run.metadata.get("git", {})
        if not git_options.get("requested"):
            return self.get_durable_report(run_id)
        if run.status != RunStatus.SUCCEEDED:
            return self.get_durable_report(run_id)
        if run.verifier_status != "passed" or run.reviewer_status != "approved":
            self.state_store.update_run_details(
                run_id,
                finalization_error=(
                    "Git finalization blocked because verifier and reviewer status "
                    "are not both successful."
                ),
                event_type="git_finalization_blocked",
            )
            return self.get_durable_report(run_id)

        changed_files = run.changed_files
        changes = self._repository_changes(changed_files)
        current_changes = set(self.git_repository.changed_files())
        pending_files = [
            path for path in changes.commit_files if path in current_changes
        ]
        self.state_store.update_run_details(
            run_id,
            changed_files=changed_files,
            event_type="changed_files_detected",
        )
        run = self.state_store.get_run(run_id)
        commit_identifier = run.commit_identifier

        if git_options.get("commit_changes") and not commit_identifier:
            if pending_files:
                message = generate_commit_message(run.original_task, pending_files)
                self.state_store.record_event(
                    run_id,
                    "commit_creation_started",
                    {"message": message, "changed_file_count": len(pending_files)},
                )
                try:
                    commit_identifier = self._commit_changed_files(
                        message,
                        pending_files,
                    )
                except GitRepositoryError as exc:
                    self.state_store.update_run_details(
                        run_id,
                        finalization_error=str(exc),
                        event_type="commit_creation_outcome_unknown",
                    )
                    return self.get_durable_report(run_id)
            else:
                current_head = self.git_repository.head_commit()
                initial_head = git_options.get("initial_head")
                if (
                    initial_head
                    and current_head != initial_head
                    and self._has_unresolved_commit_creation(run_id)
                ):
                    # A crash may occur after git commit succeeds but before SQLite
                    # records the identifier. A moved clean HEAD is reconciled rather
                    # than creating a duplicate commit.
                    commit_identifier = current_head
                else:
                    self.state_store.update_run_details(
                        run_id,
                        finalization_error=(
                            "No relevant changed files are available to commit; "
                            "generated and ignored artifacts were skipped."
                        ),
                        event_type="commit_creation_skipped",
                    )
                    return self.get_durable_report(run_id)

            self.state_store.update_run_details(
                run_id,
                commit_identifier=commit_identifier,
                event_type="commit_created",
                clear_finalization_error=True,
            )

        run = self.state_store.get_run(run_id)
        if not git_options.get("open_pr") or run.pr_url:
            return self.get_durable_report(run_id)
        if not git_options.get("commit_changes") or not run.commit_identifier:
            self.state_store.update_run_details(
                run_id,
                finalization_error="PR creation requires a successful commit.",
                event_type="pr_creation_failed",
            )
            return self.get_durable_report(run_id)

        if self._has_unresolved_pr_creation(run_id):
            self.state_store.update_run_details(
                run_id,
                finalization_error=(
                    "A previous PR creation attempt has an unknown outcome. "
                    "Inspect the remote before retrying to avoid a duplicate PR."
                ),
                event_type="pr_creation_recovery_blocked",
            )
            return self.get_durable_report(run_id)

        branch = git_options.get("branch") or self.git_repository.current_branch()
        title = generate_commit_message(run.original_task, changes.commit_files)
        body = build_pr_body(
            user_task=run.original_task,
            planner_summary=self._planner_summary(run.planner_output),
            verifier_result={"passed": True, "command": [], "reason": "Durable run"},
            reviewer_result={"approved": True, "summary": "Durable tasks approved"},
            changed_files=changes.commit_files,
            test_command=None,
        )
        self.state_store.record_event(
            run_id,
            "pr_creation_started",
            {"base": git_options.get("pr_base", "main"), "head": branch},
        )
        try:
            self.git_repository.push_branch(branch)
            pr_result = self.pr_client.create_pr(
                title=title,
                body=body,
                base=git_options.get("pr_base", "main"),
                head=branch,
            )
        except Exception as exc:
            self.state_store.update_run_details(
                run_id,
                finalization_error=str(exc),
                event_type="pr_creation_outcome_unknown",
            )
            return self.get_durable_report(run_id)

        if not pr_result.startswith("http"):
            self.state_store.update_run_details(
                run_id,
                finalization_error=pr_result,
                event_type="pr_creation_failed",
            )
            return self.get_durable_report(run_id)
        self.state_store.update_run_details(
            run_id,
            pr_url=pr_result,
            event_type="pr_created",
            clear_finalization_error=True,
        )
        return self.get_durable_report(run_id)

    def _commit_changed_files(self, message: str, changed_files: list[str]) -> str:
        commit = self.git_repository.commit
        if "paths" in inspect.signature(commit).parameters:
            return commit(message, paths=changed_files)
        return commit(message)

    def _repository_changes(self, paths: list[str]) -> RepositoryChangeSet:
        if not paths:
            return RepositoryChangeSet(
                changed_files=[],
                relevant_files=[],
                generated_files=[],
                ignored_files=[],
                tracked_generated_files=[],
                review_files=[],
                review_excluded_files=[],
                commit_files=[],
            )
        change_set = getattr(self.git_repository, "change_set", None)
        if change_set is not None:
            return change_set(paths)
        return RepositoryChangeSet(
            changed_files=paths,
            relevant_files=paths,
            generated_files=[],
            ignored_files=[],
            tracked_generated_files=[],
            review_files=paths,
            review_excluded_files=[],
            commit_files=paths,
        )

    def _all_changed_files(self) -> list[str]:
        all_changed = getattr(self.git_repository, "all_changed_files", None)
        try:
            if all_changed is not None:
                return all_changed()
            return self.git_repository.changed_files()
        except WorkspaceRepositoryMismatch:
            raise
        except GitRepositoryError:
            return []

    def _fallback_review_diff(self, changes: RepositoryChangeSet) -> str:
        full_diff = getattr(self.git_repository, "full_diff", None)
        if full_diff is not None:
            try:
                return full_diff(max_chars=30_000, paths=changes.review_files)
            except WorkspaceRepositoryMismatch:
                raise
            except GitRepositoryError:
                pass
        return self.git.git_diff()

    def _call_reviewer(
        self,
        *,
        user_task: str,
        plan: dict[str, Any],
        git_diff: str,
        test_output: str | None,
        changes: RepositoryChangeSet,
        reviewer=None,
    ) -> dict[str, Any]:
        selected_reviewer = reviewer or self.reviewer
        arguments = {
            "user_task": user_task,
            "plan": plan,
            "git_diff": git_diff,
            "test_output": test_output,
            "relevant_changed_files": changes.relevant_files,
            "generated_artifacts": changes.generated_files,
            "ignored_files": changes.ignored_files,
            "tracked_generated_artifacts": changes.tracked_generated_files,
        }
        parameters = inspect.signature(selected_reviewer.review).parameters.values()
        if not any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        ):
            supported = {parameter.name for parameter in parameters}
            arguments = {
                name: value
                for name, value in arguments.items()
                if name in supported
            }
        return selected_reviewer.review(**arguments)

    def _has_unresolved_pr_creation(self, run_id: str) -> bool:
        return self._has_unresolved_external_event(
            run_id,
            started="pr_creation_started",
            resolved={"pr_created", "pr_creation_failed"},
        )

    def _has_unresolved_commit_creation(self, run_id: str) -> bool:
        return self._has_unresolved_external_event(
            run_id,
            started="commit_creation_started",
            resolved={"commit_created"},
        )

    def _has_unresolved_external_event(
        self,
        run_id: str,
        *,
        started: str,
        resolved: set[str],
    ) -> bool:
        events = self.state_store.list_events(run_id)
        last_started = max(
            (
                event["event_id"]
                for event in events
                if event["event_type"] == started
            ),
            default=0,
        )
        last_resolved = max(
            (
                event["event_id"]
                for event in events
                if event["event_type"] in resolved
            ),
            default=0,
        )
        return last_started > last_resolved

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

        changed_files = self._all_changed_files()
        changes = self._repository_changes(changed_files)
        self.logger.log("changed_files_detected", {"files": changed_files})

        if not approved or not verifier_result.get("passed"):
            return changed_files, None, None, None

        commit_hash = None
        if self.commit_changes:
            if not changes.commit_files:
                raise GitRepositoryError(
                    "No relevant changed files are available to commit; generated "
                    "and ignored artifacts were skipped."
                )
            message = generate_commit_message(user_task, changes.commit_files)
            commit_hash = self._commit_changed_files(message, changes.commit_files)
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
        title = generate_commit_message(user_task, changes.commit_files)
        body = build_pr_body(
            user_task=user_task,
            planner_summary=self._planner_summary(plan),
            verifier_result=verifier_result,
            reviewer_result=reviewer_result,
            changed_files=changes.commit_files,
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
        changed_files = self._all_changed_files()
        changes = self._repository_changes(changed_files)
        git_diff = self._fallback_review_diff(changes)
        with model_request_context(run_id=self.logger.run_id):
            reviewer_result = self._call_reviewer(
                user_task=user_task,
                plan=plan,
                git_diff=git_diff,
                test_output=verifier_result.get("output"),
                changes=changes,
            )
        self.logger.log(
            "reviewer_output",
            {
                "approved": reviewer_result["approved"],
                "issue_count": len(reviewer_result.get("issues", [])),
                "summary_chars": len(reviewer_result.get("summary", "")),
                "required_fix_count": len(
                    reviewer_result.get("required_fixes", [])
                ),
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


def _last_model_result(agent) -> dict[str, Any] | None:
    result = getattr(getattr(agent, "model", None), "last_result", None)
    return result.event_metadata() if result is not None else None


def _plan_log_metadata(plan: dict[str, Any]) -> dict[str, Any]:
    steps = plan.get("steps", [])
    return {
        "goal_chars": len(str(plan.get("goal", ""))),
        "step_count": len(steps) if isinstance(steps, list) else 0,
        "risks": [
            step.get("risk")
            for step in steps
            if isinstance(step, dict) and step.get("risk")
        ],
    }


def _verifier_log_metadata(result: dict[str, Any]) -> dict[str, Any]:
    output = result.get("output")
    return {
        "passed": bool(result.get("passed")),
        "command": result.get("command", []),
        "exit_code": result.get("exit_code"),
        "reason_chars": len(str(result.get("reason", ""))),
        "output_chars": len(output) if isinstance(output, str) else 0,
    }
