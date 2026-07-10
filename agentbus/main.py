import argparse

from agentbus.config import AgentBusConfig
from agentbus.execution.engine import DurableExecutionEngine, DurableExecutionError
from agentbus.execution.models import ExecutionReport, RunStatus
from agentbus.execution.state_store import StateStore, StateStoreError
from agentbus.memory.run_log import RunLogger
from agentbus.repo.context_pack import ContextPackBuilder
from agentbus.repo.scanner import RepoScanner
from agentbus.repo.test_detection import TestCommandDetector
from agentbus.runtime.loop import AgentLoop
from agentbus.runtime.orchestrator import MultiAgentOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AgentBus Local Runner.")
    parser.add_argument("task", nargs="?", help="Task for the local runner.")
    parser.add_argument(
        "--workflow",
        choices=["single", "multi"],
        default="single",
        help="Select single-agent or multi-agent workflow.",
    )
    parser.add_argument("--model", help="Override the Ollama model name.")
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
    parser.add_argument("--reason", help="Reason for an approval, rejection, or cancellation.")
    args = parser.parse_args()
    has_operation = any(
        [
            args.resume,
            args.list_runs,
            args.show_run,
            args.approve,
            args.reject,
            args.cancel_run,
        ]
    )
    if has_operation and args.task:
        parser.error("durable run operations cannot be combined with a new task")
    if has_operation and args.durable:
        parser.error("--durable creates a new run and cannot be combined with run operations")
    if args.durable and args.workflow != "multi":
        parser.error("--durable requires --workflow multi")
    return args


def main() -> int:
    args = parse_args()

    print("AgentBus Local Runner")
    print("---------------------")

    config = AgentBusConfig.from_env().with_overrides(
        model_name=args.model,
        workspace_dir=args.workspace,
        max_steps=args.max_steps,
    )

    try:
        operation_result = _handle_durable_operation(args, config)
    except (StateStoreError, DurableExecutionError, ValueError) as exc:
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
    print(f"Model: {config.model_name}")
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
            except (StateStoreError, DurableExecutionError, ValueError) as exc:
                print(f"Error: {exc}")
                return 1
            print(render_execution_report(report))
            return _report_exit_code(report)

        result = orchestrator.run(task)

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
    result = loop.run(task)

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
        resume_config = config.with_overrides(
            model_name=persisted_run.model,
            workspace_dir=persisted_run.workspace,
        )
        orchestrator = MultiAgentOrchestrator(
            config=resume_config,
            state_store=store,
        )
        report = orchestrator.resume_durable(args.resume)
        print(render_execution_report(report))
        return _report_exit_code(report)

    return None


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
    if report.changed_files:
        lines.append("Changed files: " + ", ".join(report.changed_files))
    if report.commit_identifier:
        lines.append(f"Commit: {report.commit_identifier}")
    if report.pr_url:
        lines.append(f"PR: {report.pr_url}")
    if report.finalization_error:
        lines.append(f"Git/PR error: {report.finalization_error}")
    if report.failure_reason:
        lines.append(f"Reason: {report.failure_reason}")
    if report.resume_command:
        lines.append(f"Resume: {report.resume_command}")
    return "\n".join(lines)


def _report_exit_code(report: ExecutionReport) -> int:
    return 1 if report.status == RunStatus.FAILED else 0


def build_context_pack(config: AgentBusConfig, task: str | None = None) -> str:
    scan_result = RepoScanner(workspace=config.workspace_dir).scan()
    test_detection = TestCommandDetector(workspace=config.workspace_dir).detect()
    return ContextPackBuilder().build(scan_result, test_detection, user_task=task)


if __name__ == "__main__":
    raise SystemExit(main())
