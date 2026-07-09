import subprocess
import json
from typing import Dict, Any, List, Optional


MAX_DIFF_CHARS = 50_000


def run_cmd(cmd: List[str], cwd: Optional[str] = None, timeout: int = 10) -> str:
    return subprocess.check_output(
        cmd,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
        timeout=timeout
    )


def git_diff(cwd: Optional[str] = None) -> Dict[str, Any]:
    try:
        run_cmd(["git", "--version"], cwd=cwd)
    except Exception as e:
        return {"error": f"git not available: {e}"}

    try:
        inside_repo = run_cmd(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=cwd
        ).strip()

        if inside_repo != "true":
            return {"error": "not inside a git repository"}
    except Exception as e:
        return {"error": f"not inside a git repository: {e}"}

    try:
        status_output = run_cmd(
            ["git", "status", "--porcelain=v1"],
            cwd=cwd
        ).splitlines()

        files_changed = []
        untracked_files = []

        for line in status_output:
            if not line:
                continue

            status = line[:2]
            path = line[3:]

            files_changed.append(path)

            if status == "??":
                untracked_files.append(path)

    except subprocess.CalledProcessError:
        files_changed = []
        untracked_files = []

    try:
        staged_diff = run_cmd(
            ["git", "diff", "--staged", "--no-color"],
            cwd=cwd
        )

        unstaged_diff = run_cmd(
            ["git", "diff", "--no-color"],
            cwd=cwd
        )

        full_diff = (staged_diff + "\n" + unstaged_diff).strip()

    except subprocess.CalledProcessError as e:
        full_diff = f"Error getting diff: {e.output}"

    truncated = False
    if len(full_diff) > MAX_DIFF_CHARS:
        full_diff = full_diff[:MAX_DIFF_CHARS] + "\n\n[diff truncated]"
        truncated = True

    if files_changed:
        summary = f"{len(files_changed)} file(s) changed: " + ", ".join(files_changed)
    else:
        summary = "No files changed"

    return {
        "files_changed": files_changed,
        "untracked_files": untracked_files,
        "diff_text": full_diff,
        "summary": summary,
        "truncated": truncated
    }


if __name__ == "__main__":
    print(json.dumps(git_diff(), indent=2))