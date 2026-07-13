import json
from pathlib import Path
from typing import Any

from agentbus.git.repository import GitRepository, GitRepositoryError


MAX_DIFF_CHARS = 50_000


def git_diff(cwd: str | None = None) -> dict[str, Any]:
    workspace = Path(cwd).resolve() if cwd is not None else Path.cwd().resolve()
    repository = GitRepository(str(workspace))
    try:
        files_changed = repository.changed_files()
        full_diff = repository.full_diff(max_chars=MAX_DIFF_CHARS)
    except GitRepositoryError as exc:
        return {"error": str(exc)}

    return {
        "files_changed": files_changed,
        "untracked_files": [],
        "diff_text": "" if full_diff == "No diff." else full_diff,
        "summary": (
            f"{len(files_changed)} file(s) changed: " + ", ".join(files_changed)
            if files_changed
            else "No files changed"
        ),
        "truncated": full_diff.endswith("[diff truncated]"),
    }


if __name__ == "__main__":
    print(json.dumps(git_diff(), indent=2))
