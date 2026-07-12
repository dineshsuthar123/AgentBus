from pathlib import Path

from agentbus.git.repository import GitRepository, WorkspaceRepositoryMismatch


class GitTools:
    def __init__(self, workspace: str = "workspace", max_diff_chars: int = 30_000):
        self.workspace = Path(workspace).expanduser().resolve()
        self.max_diff_chars = max_diff_chars
        self.repository = GitRepository(str(self.workspace))

    def git_diff(self) -> str:
        try:
            return self.repository.review_diff(max_chars=self.max_diff_chars)
        except WorkspaceRepositoryMismatch:
            raise
        except Exception as exc:
            return f"git_diff error: {exc}"
