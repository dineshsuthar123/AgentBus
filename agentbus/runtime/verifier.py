from contextlib import nullcontext
import hashlib
from pathlib import Path
from typing import Any

from agentbus.config import AgentBusConfig
from agentbus.execution.cancellation import CancellationToken
from agentbus.repo.test_detection import TestCommandDetector
from agentbus.tools.command import CommandTools
from agentbus.tools.protocol import ToolCapabilityName, ToolInvocationStatus
from agentbus.tools.runtime import ManagedToolRuntime


class Verifier:
    def __init__(
        self,
        config: AgentBusConfig | None = None,
        command: list[str] | None = None,
        command_tools: CommandTools | None = None,
        test_detector: TestCommandDetector | None = None,
        cancellation: CancellationToken | None = None,
    ):
        self.config = config or AgentBusConfig.from_env()
        self.workspace = self.config.workspace_path
        self.command = command
        self.test_detector = test_detector or TestCommandDetector(
            workspace=str(self.workspace)
        )
        self.command_tools = command_tools or CommandTools(
            workspace=str(self.workspace),
            timeout_seconds=self.config.command_timeout_seconds,
        )
        self.cancellation = cancellation

    def verify(
        self,
        *,
        require_command: bool = False,
        tool_runtime: ManagedToolRuntime | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        invocation_key: str = "verification",
        workspace_trusted: bool = True,
        provider_consented: bool = True,
    ) -> dict[str, Any]:
        self._checkpoint("before-detection")
        detection = None
        command = self.command
        auto_detected = command is None

        if command is None:
            detection = self.test_detector.detect()
            command = detection.get("command")
        self._checkpoint("after-detection")

        if command is None:
            return {
                "command": [],
                "exit_code": 1 if require_command else 0,
                "passed": not require_command,
                "output": "No tests detected.",
                "reason": (
                    (
                        "Final verification requires a detected or configured test "
                        "command."
                        if require_command
                        else detection.get("reason", "No test command detected")
                    )
                    if detection
                    else "No test command detected"
                ),
            }

        execution_command = list(command)
        pytest_cache_disabled = False
        if auto_detected and command == ["python", "-m", "pytest"]:
            execution_command = [
                "python",
                "-B",
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
            ]
            pytest_cache_disabled = True
        executable = Path(execution_command[0]).name.lower().replace(".exe", "")
        python_verification = executable in {"python", "python3", "pytest"}
        environment_overrides = (
            {"PYTHONDONTWRITEBYTECODE": "1"} if python_verification else None
        )
        if tool_runtime is not None:
            if run_id is None or task_id is None:
                raise ValueError(
                    "Managed verification requires explicit run and task IDs."
                )
            return self._verify_managed(
                execution_command,
                detection=detection,
                tool_runtime=tool_runtime,
                run_id=run_id,
                task_id=task_id,
                invocation_key=invocation_key,
                workspace_trusted=workspace_trusted,
                provider_consented=provider_consented,
                python_verification=python_verification,
                pytest_cache_disabled=pytest_cache_disabled,
            )
        operation = (
            self.cancellation.operation(
                "verifier.command",
                source="verifier",
                interruptible=False,
            )
            if self.cancellation is not None
            else nullcontext()
        )
        with operation:
            result = self.command_tools.run_command_result(
                execution_command,
                environment_overrides=environment_overrides,
            )
        self._checkpoint("after-command")
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
            "artifact_suppression_active": python_verification,
            "pytest_cache_disabled": pytest_cache_disabled,
        }

    def _verify_managed(
        self,
        command: list[str],
        *,
        detection: dict[str, Any] | None,
        tool_runtime: ManagedToolRuntime,
        run_id: str,
        task_id: str,
        invocation_key: str,
        workspace_trusted: bool,
        provider_consented: bool,
        python_verification: bool,
        pytest_cache_disabled: bool,
    ) -> dict[str, Any]:
        arguments = {
            "executable": command[0],
            "arguments": command[1:],
        }
        idempotency_key = f"verifier:{invocation_key}"
        call = tool_runtime.prepare_model_call(
            tool_name="test.execute",
            arguments=arguments,
            expected_capabilities=(
                ToolCapabilityName.TEST_EXECUTE,
                ToolCapabilityName.PROCESS_EXECUTE,
            ),
            run_id=run_id,
            task_id=task_id,
            caller_role="verifier",
            workspace_trusted=workspace_trusted,
            provider_consented=provider_consented,
            timeout_seconds=float(self.config.command_timeout_seconds),
            idempotency_key=idempotency_key,
        )
        digest = hashlib.sha256(
            f"{run_id}\0{task_id}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()[:40]
        response = tool_runtime.invoke(
            call,
            run_id=run_id,
            task_id=task_id,
            caller_role="verifier",
            workspace_trusted=workspace_trusted,
            provider_consented=provider_consented,
            invocation_id=f"tool-{digest}",
        )
        result = response.result
        if response.awaiting_approval:
            output = "Verification is awaiting exact tool approval."
            exit_code = None
            passed = False
        elif result is None:
            output = "Verification tool has not reached a terminal state."
            exit_code = None
            passed = False
        else:
            output = result.stdout
            if result.stderr:
                output = f"{output}\n{result.stderr}" if output else result.stderr
            exit_code = result.exit_code
            passed = (
                result.status == ToolInvocationStatus.SUCCEEDED
                and result.exit_code in {None, 0}
                and bool(result.structured_output.get("passed", True))
            )
        self._checkpoint("after-command")
        return {
            "command": command,
            "exit_code": exit_code,
            "passed": passed,
            "output": output,
            "reason": (
                detection.get("reason", "Explicit verifier command")
                if detection
                else "Explicit verifier command"
            ),
            "artifact_suppression_active": python_verification,
            "pytest_cache_disabled": pytest_cache_disabled,
            "tool_invocation_id": response.invocation.invocation_id,
        }

    def _checkpoint(self, stage: str) -> None:
        if self.cancellation is not None:
            self.cancellation.checkpoint("verifier", stage=stage)
