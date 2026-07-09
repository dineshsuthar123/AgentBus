from agentbus.runtime.loop import AgentLoop


def main():
    print("AgentBus Local Runner")
    print("---------------------")

    task = input("Task: ").strip()

    if not task:
        print("No task provided.")
        return

    loop = AgentLoop()
    result = loop.run(task)

    print("\nFinal result:")
    print(result)


if __name__ == "__main__":
    main()