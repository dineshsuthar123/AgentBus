import argparse

from agentbus.config import AgentBusConfig
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
    return parser.parse_args()


def main():
    args = parse_args()

    print("AgentBus Local Runner")
    print("---------------------")

    task = args.task or input("Task: ").strip()

    if not task:
        print("No task provided.")
        return

    config = AgentBusConfig.from_env().with_overrides(
        model_name=args.model,
        workspace_dir=args.workspace,
        max_steps=args.max_steps,
    )

    print(f"Task: {task}")
    print(f"Workspace: {config.workspace_dir}")
    print(f"Model: {config.model_name}")
    print(f"Workflow: {args.workflow}")

    if args.workflow == "multi":
        orchestrator = MultiAgentOrchestrator(config=config)
        result = orchestrator.run(task)

        print(f"Planner: {result.planner_summary}")
        print(
            "Verifier: "
            f"{'passed' if result.verifier_result['passed'] else 'failed'} "
            f"({result.verifier_result['command']})"
        )
        print(f"Reviewer approved: {result.approved}")
        print("\nFinal result:")
        print(result.final_summary)
        return

    loop = AgentLoop(config=config)
    result = loop.run(task)

    print("\nFinal result:")
    print(result)


if __name__ == "__main__":
    main()
