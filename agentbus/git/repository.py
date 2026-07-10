import re
import subprocess
from pathlib import Path


class GitRepositoryError(RuntimeError):
    """Raised when a safe git operation cannot be completed."""


class GitRepository:
    def __init__(self, workspace: str = "workspace", timeout_seconds: int = 60):
        self.workspace = Path(workspace).resolve()
        self.timeout_seconds = timeout_seconds

    def is_git_repo(self) -> bool:
        try:
            result = self._run(["git", "rev-parse", "--is-inside-work-tree"])
        except GitRepositoryError:
            return False

        return result == "true"

    def current_branch(self) -> str:
        return self._run(["git", "branch", "--show-current"])

    def head_commit(self, short: bool = True) -> str:
        command = ["git", "rev-parse"]
        if short:
            command.append("--short")
        command.append("HEAD")
        return self._run(command)

    def has_uncommitted_changes(self) -> bool:
        return bool(self._run(["git", "status", "--porcelain"]))

    def create_branch(self, branch_name: str) -> str:
        self._validate_branch_name(branch_name)
        self._run(["git", "switch", "-c", branch_name])
        return f"Created branch: {branch_name}"

    def checkout_branch(self, branch_name: str) -> str:
        self._validate_branch_name(branch_name)
        self._run(["git", "switch", branch_name])
        return f"Checked out branch: {branch_name}"

    def diff_summary(self) -> str:
        status = self._run(["git", "status", "--short"])
        diff_stat = self._run(["git", "diff", "--stat"])
        staged_stat = self._run(["git", "diff", "--cached", "--stat"])
        summary = "\n".join(part for part in [status, staged_stat, diff_stat] if part)
        return summary or "No changes."

    def full_diff(self, max_chars: int = 30_000) -> str:
        staged = self._run(["git", "diff", "--cached", "--no-color"])
        unstaged = self._run(["git", "diff", "--no-color"])
        diff = "\n".join(part for part in [staged, unstaged] if part)

        if not diff:
            return "No diff."

        if len(diff) > max_chars:
            return diff[:max_chars] + "\n\n[diff truncated]"

        return diff

    def changed_files(self) -> list[str]:
        output = self._run(["git", "status", "--porcelain"])
        files = []

        for line in output.splitlines():
            if not line:
                continue

            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.append(path)

        return files

    def commit(self, message: str) -> str:
        if not message.strip():
            raise GitRepositoryError("Commit message must not be empty.")

        self._run(["git", "add", "--all"])

        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            shell=False,
        )

        if staged.returncode == 0:
            raise GitRepositoryError("No staged changes to commit.")

        if staged.returncode not in {0, 1}:
            raise GitRepositoryError(
                f"Unable to inspect staged changes: {staged.stderr.strip()}"
            )

        self._run(["git", "commit", "-m", message])
        return self._run(["git", "rev-parse", "--short", "HEAD"])

    def remote_url(self) -> str | None:
        try:
            return self._run(["git", "remote", "get-url", "origin"])
        except GitRepositoryError:
            return None

    def push_branch(self, branch_name: str | None = None) -> str:
        branch = branch_name or self.current_branch()
        self._validate_branch_name(branch)
        self._run(["git", "push", "-u", "origin", branch])
        return f"Pushed branch: {branch}"

    def _run(self, command: list[str]) -> str:
        result = subprocess.run(
            command,
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            shell=False,
        )

        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            raise GitRepositoryError(
                f"Git command failed ({' '.join(command)}): {error}"
            )

        return result.stdout.strip()

    def _validate_branch_name(self, branch_name: str) -> None:
        if not branch_name or branch_name.startswith("-"):
            raise GitRepositoryError("Branch name is invalid.")

        if ".." in branch_name or "@{" in branch_name:
            raise GitRepositoryError("Branch name contains unsafe git syntax.")

        if branch_name.endswith("/") or branch_name.endswith("."):
            raise GitRepositoryError("Branch name has an invalid ending.")

        if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch_name):
            raise GitRepositoryError("Branch name contains unsafe characters.")
