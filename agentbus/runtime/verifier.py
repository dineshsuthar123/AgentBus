from pathlib import Path
from typing import Any

from agentbus.config import AgentBusConfig
from agentbus.tools.command import CommandTools


class Verifier:
    def __init__(
        self,
        config: AgentBusConfig | None = None,
        command: list[str] | None = None,
        command_tools: CommandTools | None = None,
    ):
        self.config = config or AgentBusConfig.from_env()
        self.workspace = Path(self.config.workspace_dir).resolve()
        self.command = command
        self.command_tools = command_tools or CommandTools(
            workspace=self.config.workspace_dir,
            timeout_seconds=self.config.command_timeout_seconds,
        )

    def verify(self) -> dict[str, Any]:
        command = self.command or self._default_command()

        if command is None:
            return {
                "command": [],
                "exit_code": 0,
                "passed": True,
                "output": "No tests detected.",
            }

        result = self.command_tools.run_command_result(command)
        return {
            "command": result["command"],
            "exit_code": result["exit_code"],
            "passed": result["passed"],
            "output": result["output"],
        }

    def _default_command(self) -> list[str] | None:
        if (self.workspace / "tests").is_dir():
            return ["python", "-m", "pytest"]

        return None
