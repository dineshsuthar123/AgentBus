from pathlib import Path
from typing import Any

from agentbus.config import AgentBusConfig
from agentbus.repo.test_detection import TestCommandDetector
from agentbus.tools.command import CommandTools


class Verifier:
    def __init__(
        self,
        config: AgentBusConfig | None = None,
        command: list[str] | None = None,
        command_tools: CommandTools | None = None,
        test_detector: TestCommandDetector | None = None,
    ):
        self.config = config or AgentBusConfig.from_env()
        self.workspace = Path(self.config.workspace_dir).resolve()
        self.command = command
        self.test_detector = test_detector or TestCommandDetector(
            workspace=self.config.workspace_dir
        )
        self.command_tools = command_tools or CommandTools(
            workspace=self.config.workspace_dir,
            timeout_seconds=self.config.command_timeout_seconds,
        )

    def verify(self) -> dict[str, Any]:
        detection = None
        command = self.command

        if command is None:
            detection = self.test_detector.detect()
            command = detection.get("command")

        if command is None:
            return {
                "command": [],
                "exit_code": 0,
                "passed": True,
                "output": "No tests detected.",
                "reason": (
                    detection.get("reason", "No test command detected")
                    if detection
                    else "No test command detected"
                ),
            }

        result = self.command_tools.run_command_result(command)
        return {
            "command": result["command"],
            "exit_code": result["exit_code"],
            "passed": result["passed"],
            "output": result["output"],
            "reason": (
                detection.get("reason", "Explicit verifier command")
                if detection
                else "Explicit verifier command"
            ),
        }
