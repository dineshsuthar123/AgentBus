import argparse
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agentbus.config import AgentBusConfig
from agentbus.configuration import resolve_configuration
from agentbus.execution.engine import DurableExecutionEngine, DurableExecutionError
from agentbus.execution.leases import LeaseError, LeaseService
from agentbus.execution.models import ExecutionReport, RunStatus
from agentbus.execution.state_store import StateStore, StateStoreError
from agentbus.git.repository import GitRepositoryError
from agentbus.memory.run_log import RunLogger
from agentbus.models.errors import ModelProviderError
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole
from agentbus.repo.context_pack import ContextPackBuilder
from agentbus.repo.scanner import RepoScanner
from agentbus.repo.test_detection import TestCommandDetector
from agentbus.runtime.loop import AgentLoop
from agentbus.runtime.orchestrator import MultiAgentOrchestrator
from agentbus.security.redaction import safe_endpoint_host
from agentbus.worktrees.errors import WorktreeError
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.models import WorktreeStatus


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AgentBus Local Runner.")
    parser.add_argument("task", nargs="?", help="Task for the local runner.")
    parser.add_argument(
        "--config",
        help="Explicit TOML or JSON configuration file (no parent-directory search).",
    )
    parser.add_argument(
        "--workflow",
        choices=["single", "multi"],
        default="single",
        help="Select single-agent or multi-agent workflow.",
    )
    parser.add_argument(
        "--provider",
        choices=["ollama", "azure"],
        help="Override the primary model provider.",
    )
    parser.add_argument(
        "--fallback-provider",
        choices=["ollama", "azure"],
        help="Override the explicitly configured fallback provider.",
    )
    parser.add_argument(
        "--enable-provider-fallback",
        action="store_true",
        default=None,
        help="Enable conservative Azure-to-Ollama fallback.",
    )
    parser.add_argument("--model", help="Override the model or deployment name.")
    parser.add_argument("--planner-model", help="Override the planner model/deployment.")
    parser.add_argument("--coder-model", help="Override the coder model/deployment.")
    parser.add_argument("--reviewer-model", help="Override the reviewer model/deployment.")
    parser.add_argument(
        "--model-timeout",
        type=float,
        help="Override the model request timeout in seconds.",
    )
    parser.add_argument("--workspace", help="Override the workspace directory.")
    parser.add_argument("--max-steps", type=int, help="Override the maximum step count.")
    parser.add_argument(
        "--show-context",
        action="store_true",
        help="Print the repo context pack before running, or exit if no task is provided.",
    )
    parser.add_argument(
        "--create-branch",
        action="store_true",
        help="Create a safe task branch before running the multi-agent workflow.",
    )
    parser.add_argument("--branch-name", help="Explicit branch name to create.")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit approved multi-agent changes after verification.",
    )
    parser.add_argument(
        "--open-pr",
        action="store_true",
        help="Push the committed branch and open a GitHub PR. Requires gh CLI.",
    )
    parser.add_argument("--pr-base", default="main", help="Base branch for PR creation.")
    parser.add_argument(
        "--durable",
        action="store_true",
        help="Persist and execute a multi-agent task graph that can be resumed.",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        default=None,
        help="Run independent durable tasks concurrently in isolated worktrees.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        help="Maximum local workers for an explicitly parallel durable run.",
    )
    parser.add_argument(
        "--worktree-root",
        help="Canonical root for AgentBus-owned worktrees, outside the repository.",
    )
    parser.add_argument(
        "--keep-worktrees",
        action="store_true",
        default=None,
        help="Retain task and integration worktrees for diagnostics.",
    )
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--resume", metavar="RUN_ID", help="Resume a durable run.")
    operations.add_argument(
        "--list-runs",
        action="store_true",
        help="List persisted durable runs without executing them.",
    )
    operations.add_argument(
        "--show-run",
        metavar="RUN_ID",
        help="Show one durable run without executing it.",
    )
    operations.add_argument(
        "--approve",
        metavar="RUN_ID:TASK_ID",
        help="Approve a high-risk durable task.",
    )
    operations.add_argument(
        "--reject",
        metavar="RUN_ID:TASK_ID",
        help="Reject a high-risk durable task.",
    )
    operations.add_argument(
        "--cancel-run",
        metavar="RUN_ID",
        help="Cancel a durable run and prevent later execution.",
    )
    operations.add_argument(
        "--list-worktrees",
        nargs="?",
        const="*",
        metavar="RUN_ID",
        help="List persisted AgentBus worktrees, optionally for one run.",
    )
    operations.add_argument(
        "--list-workers",
        nargs="?",
        const="*",
        metavar="RUN_ID",
        help="List persisted worker leases, optionally for one run.",
    )
    operations.add_argument(
        "--show-scheduler",
        metavar="RUN_ID",
        help="Show scheduler, lease, worktree, and integration state for a run.",
    )
    operations.add_argument(
        "--recover-leases",
        metavar="RUN_ID",
        help="Transactionally expire stale leases for a durable run.",
    )
    operations.add_argument(
        "--cleanup-worktrees",
        metavar="RUN_ID",
        help="Explicitly remove only clean, validated AgentBus worktrees.",
    )
    operations.add_argument(
        "--list-providers",
        action="store_true",
        help="List supported model providers without making a request.",
    )
    operations.add_argument(
        "--show-model-config",
        action="store_true",
        help="Show redacted model routing configuration.",
    )
    operations.add_argument(
        "--check-provider",
        choices=["ollama", "azure"],
        help="Validate one provider's local configuration.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="With --check-provider, send one explicit minimal live request.",
    )
    parser.add_argument("--reason", help="Reason for an approval, rejection, or cancellation.")
    parser.add_argument(
        "--worker-id",
        help="Filter --list-workers output to one persisted worker ID.",
    )
    args = parser.parse_args(argv)
    has_operation = any(
        [
            args.resume,
            args.list_runs,
            args.show_run,
            args.approve,
            args.reject,
            args.cancel_run,
            args.list_worktrees,
            args.list_workers,
            args.show_scheduler,
            args.recover_leases,
            args.cleanup_worktrees,
            args.list_providers,
            args.show_model_config,
            args.check_provider,
        ]
    )
    if has_operation and args.task:
        parser.error("operations cannot be combined with a new task")
    if has_operation and args.durable:
        parser.error("--durable creates a new run and cannot be combined with operations")
    if args.durable and args.workflow != "multi":
        parser.error("--durable requires --workflow multi")
    parallel_options = any(
        value is not None
        for value in (
            args.parallel,
            args.max_workers,
            args.worktree_root,
            args.keep_worktrees,
        )
    )
    if parallel_options and (not args.durable or args.workflow != "multi"):
        parser.error("parallel options require --durable --workflow multi")
    if args.max_workers is not None and args.max_workers < 1:
        parser.error("--max-workers must be at least 1")
    if args.worker_id and args.list_workers is None:
        parser.error("--worker-id requires --list-workers")
    if args.live and not args.check_provider:
        parser.error("--live requires --check-provider")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    print("AgentBus Local Runner")
    print("---------------------")

    try:
        base_config = (
            AgentBusConfig.from_env()
            if args.config is None
            else resolve_configuration(config_file=args.config).config
        )
        config = base_config.with_overrides(
            model_name=args.model,
            workspace_dir=args.workspace,
            max_steps=args.max_steps,
            provider_name=args.provider,
            fallback_provider_name=args.fallback_provider,
            enable_provider_fallback=args.enable_provider_fallback,
            planner_model=args.planner_model,
            coder_model=args.coder_model,
            reviewer_model=args.reviewer_model,
            model_timeout_seconds=args.model_timeout,
            parallel_execution=args.parallel,
            max_workers=args.max_workers,
            worktree_root=args.worktree_root,
            keep_worktrees=args.keep_worktrees,
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    diagnostic_result = _handle_model_diagnostic(args, config)
    if diagnostic_result is not None:
        return diagnostic_result

    try:
        operation_result = _handle_durable_operation(args, config)
    except (
        StateStoreError,
        DurableExecutionError,
        LeaseError,
        GitRepositoryError,
        ModelProviderError,
        WorktreeError,
        ValueError,
    ) as exc:
        print(f"Error: {exc}")
        return 1
    if operation_result is not None:
        return operation_result

    task = args.task

    if args.show_context:
        print(build_context_pack(config, task))

        if not task:
            return 0

    task = task or input("Task: ").strip()

    if not task:
        print("No task provided.")
        return 1

    print(f"Task: {task}")
    print(f"Workspace: {config.workspace_dir}")
    selected_role = "coder" if args.workflow in {"single", "multi"} else "default"
    try:
        selected_model = config.resolve_model(selected_role)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    print(f"Provider: {config.provider_name}")
    print(f"Model: {selected_model}")
    print(f"Workflow: {args.workflow}")

    if args.workflow == "multi":
        orchestrator = MultiAgentOrchestrator(
            config=config,
            create_branch=args.create_branch,
            branch_name=args.branch_name,
            commit_changes=args.commit,
            open_pr=args.open_pr,
            pr_base=args.pr_base,
        )
        if args.durable:
            try:
                run_id = orchestrator.create_durable_run(task)
                print(f"Run ID: {run_id}")
                report = orchestrator.run_durable(run_id)
            except (
                StateStoreError,
                DurableExecutionError,
                GitRepositoryError,
                ModelProviderError,
                ValueError,
            ) as exc:
                print(f"Error: {exc}")
                return 1
            print(render_execution_report(report))
            return _report_exit_code(report)

        try:
            result = orchestrator.run(task)
        except (GitRepositoryError, ModelProviderError, ValueError) as exc:
            print(f"Error: {exc}")
            return 1

        print(f"Planner: {result.planner_summary}")
        print(f"Branch: {result.git_branch or '[unchanged]'}")
        print(f"Changed files: {result.changed_files or []}")
        print(
            "Verifier: "
            f"{'passed' if result.verifier_result['passed'] else 'failed'} "
            f"({result.verifier_result['command']})"
        )
        print(f"Reviewer approved: {result.approved}")
        if result.commit_hash:
            print(f"Commit: {result.commit_hash}")
        if result.pr_url:
            print(f"PR: {result.pr_url}")
        if result.pr_error:
            print(f"PR error: {result.pr_error}")
        print("\nFinal result:")
        print(result.final_summary)
        return 0

    loop = AgentLoop(config=config)
    try:
        result = loop.run(task)
    except (ModelProviderError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

    print("\nFinal result:")
    print(result)
    return 0


def _handle_durable_operation(
    args: argparse.Namespace,
    config: AgentBusConfig,
) -> int | None:
    if not any(
        [
            args.resume,
            args.list_runs,
            args.show_run,
            args.approve,
            args.reject,
            args.cancel_run,
            args.list_worktrees,
            args.list_workers,
            args.show_scheduler,
            args.recover_leases,
            args.cleanup_worktrees,
            args.list_providers,
            args.show_model_config,
            args.check_provider,
        ]
    ):
        return None

    store = StateStore(config.state_database_path)
    inspection_engine = DurableExecutionEngine(store)

    if args.list_runs:
        runs = store.list_runs()
        if not runs:
            print("No durable runs found.")
            return 0
        for run in runs:
            print(
                f"{run.run_id}  {run.status.value:<22} "
                f"{run.updated_at.isoformat()}  {run.original_task}"
            )
        return 0

    if args.show_run:
        print(render_execution_report(inspection_engine.get_report(args.show_run)))
        return 0

    if args.show_scheduler:
        print(render_execution_report(inspection_engine.get_report(args.show_scheduler)))
        return 0

    if args.list_worktrees is not None:
        run_id = None if args.list_worktrees == "*" else args.list_worktrees
        worktrees = store.list_worktrees(run_id)
        if not worktrees:
            print("No AgentBus worktrees found.")
            return 0
        for item in worktrees:
            print(
                f"{item.worktree_id}  {item.run_id}  "
                f"{item.task_id or '[integration]'}  {item.purpose.value}  "
                f"{item.status.value}  {item.path}"
            )
        return 0

    if args.list_workers is not None:
        run_id = None if args.list_workers == "*" else args.list_workers
        rows = store.list_worker_lease_rows(run_id)
        if args.worker_id:
            rows = [item for item in rows if item["worker_id"] == args.worker_id]
        if not rows:
            print("No worker leases found.")
            return 0
        for item in rows:
            print(
                f"{item['worker_id']}  {item['run_id']}  {item['task_id']}  "
                f"{item['status']}  token={item['fencing_token']}  "
                f"expires={item['expires_at']}"
            )
        return 0

    if args.recover_leases:
        run = store.get_run(args.recover_leases)
        parallel = _parallel_metadata(run)
        leases = LeaseService(
            store,
            lease_seconds=float(parallel.get("lease_seconds", 120.0)),
        )
        expired = leases.expire_stale_leases(run.run_id)
        print(f"Expired stale leases: {len(expired)}")
        for lease in expired:
            print(
                f"{lease.lease_id}  {lease.worker_id}  {lease.task_id}  "
                f"token={lease.fencing_token}"
            )
        return 0

    if args.cleanup_worktrees:
        return _cleanup_worktrees(store, args.cleanup_worktrees, config)

    if args.approve:
        run_id, task_id = _parse_run_task(args.approve)
        engine = DurableExecutionEngine(
            store,
            logger=RunLogger(log_dir=config.runs_dir, run_id=run_id),
        )
        report = engine.approve_task(run_id, task_id, args.reason)
        print(render_execution_report(report))
        return _report_exit_code(report)

    if args.reject:
        run_id, task_id = _parse_run_task(args.reject)
        engine = DurableExecutionEngine(
            store,
            logger=RunLogger(log_dir=config.runs_dir, run_id=run_id),
        )
        report = engine.reject_task(run_id, task_id, args.reason)
        print(render_execution_report(report))
        return _report_exit_code(report)

    if args.cancel_run:
        engine = DurableExecutionEngine(
            store,
            logger=RunLogger(log_dir=config.runs_dir, run_id=args.cancel_run),
        )
        report = engine.cancel_run(args.cancel_run, args.reason)
        print(render_execution_report(report))
        return _report_exit_code(report)

    if args.resume:
        persisted_run = store.get_run(args.resume)
        resume_config = _resume_model_config(config, persisted_run, args)
        orchestrator = MultiAgentOrchestrator(
            config=resume_config,
            state_store=store,
        )
        report = orchestrator.resume_durable(args.resume)
        print(render_execution_report(report))
        return _report_exit_code(report)

    return None


class ProviderSmokeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


def _handle_model_diagnostic(
    args: argparse.Namespace,
    config: AgentBusConfig,
) -> int | None:
    if args.list_providers:
        print("ollama  local/offline provider (default)")
        print("azure   Azure OpenAI v1 provider")
        return 0

    if args.show_model_config:
        print(json.dumps(config.safe_model_summary(), indent=2, sort_keys=True))
        return 0

    if not args.check_provider:
        return None

    provider = args.check_provider
    errors = _provider_configuration_errors(config, provider)
    print(f"Provider: {provider}")
    if provider == "azure":
        endpoint_host = safe_endpoint_host(config.azure_openai_endpoint)
        print(f"Endpoint host: {endpoint_host or '[not configured]'}")
        print(f"Auth mode: {config.azure_openai_auth_mode}")
        print(f"API mode: {config.azure_openai_api_mode}")
    try:
        model = config.resolve_model("default", provider=provider)
    except ValueError:
        model = None
    print(f"Model/deployment: {model or '[not configured]'}")

    if errors:
        print("Readiness: not ready")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Readiness: locally configured")
    if not args.live:
        print("Network request: skipped (use --live to opt in)")
        return 0

    print("Network request: LIVE opt-in; sending one minimal model request")
    live_config = config.with_overrides(
        provider_name=provider,
        enable_provider_fallback=False,
    )
    try:
        result = build_model_router(live_config).generate_json(
            ModelRole.DEFAULT,
            'Return exactly this JSON object: {"status":"ok"}',
            schema=ProviderSmokeOutput,
            metadata={"operation": "provider_smoke"},
        )
    except (ModelProviderError, ValueError) as exc:
        print(f"Readiness: live request failed: {exc}")
        return 1

    print("Readiness: live request succeeded")
    print(f"Resolved provider: {result.provider}")
    print(f"Resolved model/deployment: {result.model}")
    print(f"Latency seconds: {result.latency_seconds}")
    print(f"Request ID: {result.request_id or '[not provided]'}")
    print(f"Usage: {json.dumps(result.usage.model_dump(mode='json'), sort_keys=True)}")
    return 0


def build_model_router(config: AgentBusConfig) -> ModelRouter:
    return ModelRouter(config)


def _provider_configuration_errors(
    config: AgentBusConfig,
    provider: str,
) -> list[str]:
    errors: list[str] = []
    try:
        if provider == "ollama":
            config.validate_provider_configuration(provider)
        else:
            config.resolve_model("default", provider=provider)
    except (ModelProviderError, ValueError) as exc:
        errors.append(str(exc))

    if provider == "azure":
        if not config.azure_openai_endpoint:
            errors.append("Set AZURE_OPENAI_ENDPOINT.")
        if not config.azure_openai_api_key:
            errors.append("Set AZURE_OPENAI_API_KEY.")
        try:
            config.validate_azure_modes()
        except ValueError as exc:
            errors.append(str(exc))
        if config.azure_openai_endpoint:
            try:
                from agentbus.models.azure_openai import normalize_azure_v1_endpoint

                normalize_azure_v1_endpoint(config.azure_openai_endpoint)
            except ModelProviderError as exc:
                errors.append(exc.safe_message)
    return list(dict.fromkeys(errors))


def _resume_model_config(config, persisted_run, args) -> AgentBusConfig:
    routing = persisted_run.metadata.get("model_routing", {})
    if not isinstance(routing, dict):
        routing = {}
    parallel = _parallel_metadata(persisted_run)
    parallel_overrides = {
        "parallel_execution": bool(parallel.get("enabled", False)),
        "max_workers": int(parallel.get("max_workers", 1)),
        "worktree_root": parallel.get("worktree_root"),
        "keep_worktrees": bool(parallel.get("keep_worktrees", True)),
    }
    if not routing:
        return config.with_overrides(
            model_name=args.model or persisted_run.model,
            workspace_dir=persisted_run.workspace,
            **parallel_overrides,
        )
    routes = routing.get("routes", {})
    if not isinstance(routes, dict):
        routes = {}

    persisted_provider = routing.get("provider")
    provider = args.provider or (
        persisted_provider if isinstance(persisted_provider, str) else None
    )
    restore_persisted_routes = not args.provider or args.provider == persisted_provider
    fallback_provider = args.fallback_provider or routing.get("fallback_provider")
    fallback_enabled = args.enable_provider_fallback
    if fallback_enabled is None:
        fallback_enabled = bool(routing.get("fallback_enabled", False))
    if args.provider and args.provider != persisted_provider:
        fallback_enabled = bool(args.enable_provider_fallback)

    return config.with_overrides(
        model_name=(
            args.model
            or (
                _persisted_model(routes, "default") or persisted_run.model
                if restore_persisted_routes
                else config.model_name
            )
        ),
        workspace_dir=persisted_run.workspace,
        provider_name=provider,
        fallback_provider_name=(
            fallback_provider if isinstance(fallback_provider, str) else None
        ),
        enable_provider_fallback=fallback_enabled,
        planner_model=(
            args.planner_model
            or (
                _persisted_model(routes, "planner")
                if restore_persisted_routes
                else None
            )
        ),
        coder_model=(
            args.coder_model
            or (
                _persisted_model(routes, "coder")
                if restore_persisted_routes
                else None
            )
        ),
        reviewer_model=(
            args.reviewer_model
            or (
                _persisted_model(routes, "reviewer")
                if restore_persisted_routes
                else None
            )
        ),
        summarizer_model=(
            _persisted_model(routes, "summarizer")
            if restore_persisted_routes
            else None
        ),
        **parallel_overrides,
    )


def _parallel_metadata(run) -> dict:
    parallel = run.metadata.get("parallel_execution", {})
    return parallel if isinstance(parallel, dict) else {}


def _cleanup_worktrees(
    store: StateStore,
    run_id: str,
    config: AgentBusConfig,
) -> int:
    run = store.get_run(run_id)
    parallel = _parallel_metadata(run)
    root = parallel.get("worktree_root") or config.with_overrides(
        workspace_dir=run.workspace
    ).worktree_root_path
    manager = GitWorktreeManager(run.workspace, root, store)
    removed = 0
    refused = 0
    for record in store.list_worktrees(run_id):
        if record.status == WorktreeStatus.REMOVED:
            continue
        try:
            manager.mark_cleanup_pending(record.worktree_id)
            manager.remove(record.worktree_id)
            removed += 1
            print(f"Removed: {record.path}")
        except WorktreeError as exc:
            refused += 1
            print(f"Refused: {record.path}: {exc}")
    print(f"Cleanup result: {removed} removed, {refused} refused")
    return 1 if refused else 0


def _persisted_model(routes: dict, role: str) -> str | None:
    route = routes.get(role, {})
    if not isinstance(route, dict):
        return None
    model = route.get("model")
    return model if isinstance(model, str) and model else None


def _parse_run_task(value: str) -> tuple[str, str]:
    run_id, separator, task_id = value.partition(":")
    if not separator or not run_id.strip() or not task_id.strip():
        raise ValueError("Expected RUN_ID:TASK_ID.")
    return run_id.strip(), task_id.strip()


def render_execution_report(report: ExecutionReport) -> str:
    progress = report.graph_progress
    lines = [
        f"Run ID: {report.run_id}",
        f"Status: {report.status.value}",
        (
            "Tasks: "
            f"{progress.succeeded}/{progress.total} succeeded, "
            f"{progress.failed} failed, {progress.blocked} blocked, "
            f"{progress.waiting_for_approval} awaiting approval"
        ),
    ]
    if report.pending_approvals:
        lines.append("Pending approval: " + ", ".join(report.pending_approvals))
    if report.failed_tasks:
        lines.append("Failed tasks: " + ", ".join(report.failed_tasks))
    if report.blocked_tasks:
        lines.append("Blocked tasks: " + ", ".join(report.blocked_tasks))
    if report.verifier_status:
        lines.append(f"Verifier: {report.verifier_status}")
    if report.reviewer_status:
        lines.append(f"Reviewer: {report.reviewer_status}")
    lines.append(f"Workspace: {report.workspace or '[not recorded]'}")
    lines.append(
        f"Detected Git top-level: {report.git_top_level or '[not recorded]'}"
    )
    if report.reviewer_summary:
        lines.append(f"Reviewer summary: {report.reviewer_summary}")
    for issue in report.reviewer_issues:
        severity = issue.get("severity", "unknown")
        location = f" ({issue['file']})" if issue.get("file") else ""
        lines.append(
            f"Reviewer issue [{severity}]{location}: {issue.get('message', '')}"
        )
    for required_fix in report.required_fixes:
        lines.append(f"Required fix: {required_fix}")
    for failure in report.task_failures:
        lines.append(
            "Task failure: "
            f"{failure.get('task_id')} [{failure.get('category')}] "
            f"{failure.get('message')}"
        )
    if report.changed_files:
        lines.append("Changed files: " + ", ".join(report.changed_files))
    lines.append(
        "Relevant changed files: "
        + (", ".join(report.relevant_changed_files) or "[none]")
    )
    lines.append(
        "Generated artifacts detected: "
        + (", ".join(report.generated_artifacts) or "[none]")
    )
    if report.tracked_generated_artifacts:
        lines.append(
            "Tracked generated artifacts requiring review: "
            + ", ".join(report.tracked_generated_artifacts)
        )
    lines.append(
        "Ignored files detected: " + (", ".join(report.ignored_files) or "[none]")
    )
    lines.append(
        "Files eligible for commit: "
        + (", ".join(report.commit_eligible_files) or "[none]")
    )
    lines.append(
        "Files excluded from review: "
        + (", ".join(report.review_excluded_files) or "[none]")
    )
    lines.append(
        "Verifier artifact suppression: "
        + ("active" if report.verifier_artifact_suppression_active else "inactive")
    )
    if report.parallel_mode_enabled:
        lines.append("Parallel execution: enabled")
        lines.append(f"Configured max workers: {report.configured_max_workers}")
        lines.append(
            "Workers used: " + (", ".join(report.workers_used) or "[none]")
        )
        for lease in report.current_leases:
            lines.append(
                "Active lease: "
                f"{lease.get('worker_id')} -> {lease.get('task_id')} "
                f"token={lease.get('fencing_token')} expires={lease.get('expires_at')}"
            )
        for lease in report.expired_leases:
            lines.append(
                "Expired lease: "
                f"{lease.get('worker_id')} -> {lease.get('task_id')} "
                f"token={lease.get('fencing_token')}"
            )
        for task_id, path in sorted(report.task_worktrees.items()):
            lines.append(f"Task worktree: {task_id} -> {path}")
        for task_id, commit in sorted(report.task_commits.items()):
            lines.append(f"Task commit: {task_id} -> {commit}")
        if report.original_base_commit:
            lines.append(f"Original base commit: {report.original_base_commit}")
        lines.append(
            "Integration order: "
            + (", ".join(report.integration_order) or "[none]")
        )
        if report.integration_commit:
            lines.append(f"Integration commit: {report.integration_commit}")
        if report.final_branch:
            lines.append(f"Final branch: {report.final_branch}")
        for conflict in report.integration_conflicts:
            files = ", ".join(conflict.get("conflict_files", [])) or "[unknown]"
            lines.append(
                f"Integration conflict: {conflict.get('task_id')} files={files}"
            )
        for path in report.retained_worktrees:
            lines.append(f"Retained worktree: {path}")
        for recommendation in report.cleanup_recommendations:
            lines.append(f"Cleanup recommendation: {recommendation}")
    if report.commit_identifier:
        lines.append(f"Commit: {report.commit_identifier}")
    if report.pr_url:
        lines.append(f"PR: {report.pr_url}")
    if report.finalization_error:
        lines.append(f"Git/PR error: {report.finalization_error}")
    if report.failure_reason:
        lines.append(f"Reason: {report.failure_reason}")
    if report.side_effects_persisted:
        lines.append(
            "Filesystem rollback: not performed; created or modified files remain "
            "in the target workspace for inspection and manual cleanup."
        )
    if report.resume_command:
        lines.append(f"Resume: {report.resume_command}")
    return "\n".join(lines)


def _report_exit_code(report: ExecutionReport) -> int:
    return 1 if report.status == RunStatus.FAILED else 0


def build_context_pack(config: AgentBusConfig, task: str | None = None) -> str:
    workspace = str(config.workspace_path)
    scan_result = RepoScanner(workspace=workspace).scan()
    test_detection = TestCommandDetector(workspace=workspace).detect()
    return ContextPackBuilder(workspace=workspace).build(
        scan_result,
        test_detection,
        user_task=task,
    )


if __name__ == "__main__":
    raise SystemExit(main())
