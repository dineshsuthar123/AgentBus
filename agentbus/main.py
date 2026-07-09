import argparse

from agentbus.config import AgentBusConfig
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
    return parser.parse_args()


def main():
    args = parse_args()

    print("AgentBus Local Runner")
    print("---------------------")

    config = AgentBusConfig.from_env().with_overrides(
        model_name=args.model,
        workspace_dir=args.workspace,
        max_steps=args.max_steps,
    )

    task = args.task

    if args.show_context:
        print(build_context_pack(config, task))

        if not task:
            return

    task = task or input("Task: ").strip()

    if not task:
        print("No task provided.")
        return

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
        return

    loop = AgentLoop(config=config)
    result = loop.run(task)

    print("\nFinal result:")
    print(result)


def build_context_pack(config: AgentBusConfig, task: str | None = None) -> str:
    scan_result = RepoScanner(workspace=config.workspace_dir).scan()
    test_detection = TestCommandDetector(workspace=config.workspace_dir).detect()
    return ContextPackBuilder().build(scan_result, test_detection, user_task=task)


if __name__ == "__main__":
    main()
