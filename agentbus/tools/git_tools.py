import subprocess
from pathlib import Path


class GitTools:
    def __init__(self, workspace: str = "workspace"):
        self.workspace = Path(workspace).resolve()

    def git_diff(self) -> str:
        try:
            result = subprocess.run(
                ["git", "diff", "--", "."],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
                shell=False,
            )

            diff = result.stdout.strip()

            if not diff:
                return "No git diff found."

            return diff[:30_000]

        except Exception as e:
            return f"git_diff error: {str(e)}"