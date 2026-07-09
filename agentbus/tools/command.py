import subprocess
from pathlib import Path


class CommandTools:
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
        if not isinstance(command, list) or not all(
            isinstance(arg, str) for arg in command
        ):
            return "Blocked: command must be list[str]."

        if not command:
            return "Blocked: empty command."

        timeout = timeout or self.timeout_seconds
        executable = Path(command[0]).name.lower().replace(".exe", "")

        if executable not in self.allowed_commands:
            return f"Blocked command executable: {command[0]}"

        for arg in command:
            normalized = str(arg).lower()
            if normalized in self.blocked_args:
                return f"Blocked unsafe argument: {arg}"

            if any(token in normalized for token in self.blocked_shell_tokens):
                return f"Blocked shell syntax in argument: {arg}"

        try:
            result = subprocess.run(
                command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            return (
                f"Exit code: {result.returncode}\n"
                f"STDOUT:\n{stdout if stdout else '[empty]'}\n"
                f"STDERR:\n{stderr if stderr else '[empty]'}"
            )

        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout} seconds."

        except FileNotFoundError:
            return f"Command not found: {command[0]}"

        except Exception as e:
            return f"Command error: {str(e)}"
