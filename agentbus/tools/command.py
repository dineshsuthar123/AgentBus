import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path


class CommandTools:
    SAFE_ENVIRONMENT_OVERRIDES = {
        "PYTHONDONTWRITEBYTECODE": {"1"},
    }

    def __init__(self, workspace: str = "workspace", timeout_seconds: int = 90):
        self.workspace = Path(workspace).resolve()
        self.timeout_seconds = timeout_seconds

        self.allowed_commands = {
            "python",
            "python3",
            "pytest",
            "node",
            "npm",
            "java",
            "javac",
            "mvn",
            "gradle",
            "git",
        }

        self.blocked_args = {
            "rm",
            "del",
            "rmdir",
            "shutdown",
            "reboot",
            "format",
            "mkfs",
            "sudo",
            "powershell",
            "cmd",
            "curl",
            "wget",
            "scp",
            "ssh",
            "clean",
            "reset",
            "--hard",
            "--force",
            "--delete",
            "-rf",
            "-fr",
        }

        self.blocked_shell_tokens = {
            "&&",
            "||",
            "|",
            ">",
            "<",
            ";",
        }

    def run_command(self, command: list[str], timeout: int | None = None) -> str:
        return self.run_command_result(command, timeout=timeout)["output"]

    def run_command_result(
        self,
        command: list[str],
        timeout: int | None = None,
        *,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> dict:
        if not isinstance(command, list) or not all(
            isinstance(arg, str) for arg in command
        ):
            return _blocked_result(command, "Blocked: command must be list[str].")

        if not command:
            return _blocked_result(command, "Blocked: empty command.")

        timeout = timeout or self.timeout_seconds
        executable = Path(command[0]).name.lower().replace(".exe", "")
        execution_command = list(command)

        if executable not in self.allowed_commands:
            return _blocked_result(command, f"Blocked command executable: {command[0]}")

        for arg in command:
            normalized = str(arg).lower()
            if normalized in self.blocked_args:
                return _blocked_result(command, f"Blocked unsafe argument: {arg}")

            if any(token in normalized for token in self.blocked_shell_tokens):
                return _blocked_result(command, f"Blocked shell syntax in argument: {arg}")

        if executable in {"python", "python3"}:
            execution_command[0] = sys.executable

        try:
            child_environment = self._child_environment(environment_overrides)
        except ValueError as exc:
            return _blocked_result(command, f"Blocked environment override: {exc}")

        try:
            result = subprocess.run(
                execution_command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                env=child_environment,
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            output = _format_output(result.returncode, stdout, stderr)

            return {
                "command": command,
                "exit_code": result.returncode,
                "passed": result.returncode == 0,
                "stdout": stdout,
                "stderr": stderr,
                "output": output,
                "blocked": False,
            }

        except subprocess.TimeoutExpired:
            return _blocked_result(
                command,
                f"Command timed out after {timeout} seconds.",
                blocked=False,
            )

        except FileNotFoundError:
            return _blocked_result(command, f"Command not found: {command[0]}")

        except Exception as e:
            return _blocked_result(command, f"Command error: {str(e)}")

    def _child_environment(
        self,
        overrides: Mapping[str, str] | None,
    ) -> dict[str, str] | None:
        if not overrides:
            return None
        environment = os.environ.copy()
        for name, value in overrides.items():
            allowed_values = self.SAFE_ENVIRONMENT_OVERRIDES.get(name)
            if allowed_values is None or value not in allowed_values:
                raise ValueError(f"{name} is not an allowed verifier override")
            environment[name] = value
        return environment


def _format_output(exit_code: int, stdout: str, stderr: str) -> str:
    return (
        f"Exit code: {exit_code}\n"
        f"STDOUT:\n{stdout if stdout else '[empty]'}\n"
        f"STDERR:\n{stderr if stderr else '[empty]'}"
    )


def _blocked_result(command, message: str, blocked: bool = True) -> dict:
    return {
        "command": command if isinstance(command, list) else [],
        "exit_code": None,
        "passed": False,
        "stdout": "",
        "stderr": message,
        "output": message,
        "blocked": blocked,
    }
