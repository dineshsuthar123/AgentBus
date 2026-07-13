import subprocess
from pathlib import Path


class GitHubPullRequestClient:
    def __init__(self, workspace: str = "workspace", timeout_seconds: int = 60):
        self.workspace = Path(workspace).resolve()
        self.timeout_seconds = timeout_seconds

    def create_pr(
        self,
        title: str,
        body: str,
        base: str = "main",
        head: str | None = None,
    ) -> str:
        command = [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--base",
            base,
        ]

        if head:
            command.extend(["--head", head])

        try:
            result = subprocess.run(
                command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
            )
        except FileNotFoundError:
            return "GitHub CLI not found. Install gh and authenticate before opening PRs."

        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip()
            return f"GitHub PR creation failed: {details}"

        return result.stdout.strip()
