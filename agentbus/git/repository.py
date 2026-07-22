from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from agentbus.repo.artifact_policy import (
    ArtifactPolicyError,
    GeneratedArtifactPolicy,
)
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.security.redaction import redact_text, safe_child_environment
from agentbus.tools.filesystem_security import (
    ContainedPathResolver,
    FileSystemSecurityError,
    normalize_relative_tool_path,
)


_GIT_IDENTITY_ENVIRONMENT = frozenset(
    {
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_NAME",
    }
)


class GitRepositoryError(RuntimeError):
    """Raised when a safe git operation cannot be completed."""


class WorkspaceRepositoryMismatch(GitRepositoryError):
    """Raised when Git resolves the workspace to an unintended parent repository."""


@dataclass(frozen=True)
class GitStatusEntry:
    path: str
    status: str
    tracked: bool
    ignored: bool


@dataclass(frozen=True)
class RepositoryChangeSet:
    changed_files: list[str]
    relevant_files: list[str]
    generated_files: list[str]
    ignored_files: list[str]
    tracked_generated_files: list[str]
    review_files: list[str]
    review_excluded_files: list[str]
    commit_files: list[str]
    protected_files: list[str] = field(default_factory=list)

    def to_metadata(self) -> dict[str, list[str]]:
        return {
            "changed_files": self.changed_files,
            "relevant_changed_files": self.relevant_files,
            "generated_artifacts": self.generated_files,
            "ignored_files": self.ignored_files,
            "tracked_generated_artifacts": self.tracked_generated_files,
            "review_files": self.review_files,
            "review_excluded_files": self.review_excluded_files,
            "commit_eligible_files": self.commit_files,
            "protected_files": self.protected_files,
        }


class GitRepository:
    def __init__(
        self,
        workspace: str = "workspace",
        timeout_seconds: int = 60,
        artifact_policy: GeneratedArtifactPolicy | None = None,
        executable_catalog: ExecutableCatalog | None = None,
        maximum_command_output_chars: int = 4_194_304,
    ):
        if maximum_command_output_chars < 1:
            raise ValueError("maximum_command_output_chars must be positive")
        self.workspace = Path(workspace).expanduser().resolve()
        self.timeout_seconds = timeout_seconds
        self.artifact_policy = artifact_policy or GeneratedArtifactPolicy()
        self.executable_catalog = executable_catalog or ExecutableCatalog.standard(
            ("git",)
        )
        self.maximum_command_output_chars = maximum_command_output_chars
        self._validated_top_level: Path | None = None
        self._path_resolver: ContainedPathResolver | None = None

    def discover_top_level(self) -> Path:
        output = self._run_unvalidated(["git", "rev-parse", "--show-toplevel"])
        return Path(output).expanduser().resolve()

    def validate_workspace(self) -> Path:
        if not self.workspace.is_dir():
            raise GitRepositoryError(
                f"Configured workspace does not exist: {self.workspace}"
            )

        top_level = self.discover_top_level()
        if os.path.normcase(str(top_level)) != os.path.normcase(str(self.workspace)):
            raise WorkspaceRepositoryMismatch(
                "Configured workspace is not the Git repository root. "
                f"Workspace: {self.workspace}. Detected Git top-level: {top_level}. "
                "Git would walk into a parent repository, so AgentBus refused the "
                "operation. Initialize or select an isolated target repository."
            )

        self._validated_top_level = top_level
        return top_level

    def is_git_repo(self) -> bool:
        try:
            self.validate_workspace()
        except WorkspaceRepositoryMismatch:
            raise
        except GitRepositoryError:
            return False
        return True

    def current_branch(self) -> str:
        return self._run(["git", "branch", "--show-current"])

    def head_commit(self, short: bool = True) -> str:
        command = ["git", "rev-parse"]
        if short:
            command.append("--short")
        command.append("HEAD")
        return self._run(command)

    def has_uncommitted_changes(self) -> bool:
        return bool(self._status_output(include_ignored=False))

    def create_branch(self, branch_name: str) -> str:
        self._validate_branch_name(branch_name)
        self._run(["git", "switch", "-c", branch_name])
        return f"Created branch: {branch_name}"

    def create_branch_at(self, branch_name: str, commit_sha: str) -> str:
        self._validate_branch_name(branch_name)
        resolved = self._resolve_commit(commit_sha)
        self._run(["git", "branch", branch_name, resolved])
        return f"Created branch: {branch_name} at {resolved}"

    def branch_commit(self, branch_name: str) -> str | None:
        self._validate_branch_name(branch_name)
        try:
            return self._run(
                ["git", "rev-parse", "--verify", f"refs/heads/{branch_name}^{{commit}}"]
            )
        except GitRepositoryError:
            return None

    def checkout_branch(self, branch_name: str) -> str:
        self._validate_branch_name(branch_name)
        self._run(["git", "switch", branch_name])
        return f"Checked out branch: {branch_name}"

    def diff_summary(self) -> str:
        status = self._run(
            ["git", "status", "--short", "--untracked-files=all", "--", "."]
        )
        diff_stat = self._run(
            ["git", "diff", "--stat", "--no-ext-diff", "--no-textconv", "--", "."]
        )
        staged_stat = self._run(
            [
                "git",
                "diff",
                "--cached",
                "--stat",
                "--no-ext-diff",
                "--no-textconv",
                "--",
                ".",
            ]
        )
        summary = "\n".join(part for part in [status, staged_stat, diff_stat] if part)
        return summary or "No changes."

    def full_diff(
        self,
        max_chars: int = 30_000,
        paths: Iterable[str] | None = None,
    ) -> str:
        if max_chars < 1 or max_chars > self.maximum_command_output_chars:
            raise ValueError(
                "max_chars must be positive and within the command output limit"
            )
        requested = self._normalize_paths(paths)
        if paths is None:
            selected = self._exclude_protected(self.changed_files())
        else:
            protected = self._protected_paths(requested)
            if protected:
                raise GitRepositoryError(
                    "Protected repository paths cannot be included in Git diffs."
                )
            selected = requested
        if not selected:
            self.validate_workspace()
            return "No diff."
        pathspec = selected
        staged = self._run(
            [
                "git",
                "diff",
                "--cached",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--",
                *pathspec,
            ]
        )
        unstaged = self._run(
            [
                "git",
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--",
                *pathspec,
            ]
        )
        parts = [part for part in [staged, unstaged] if part]

        changed = set(selected)
        tracked = set(self._tracked_files(changed))
        for path in sorted(changed - tracked):
            parts.append(self._untracked_diff(path))

        diff = "\n".join(part for part in parts if part)
        if not diff:
            return "No diff."
        if len(diff) > max_chars:
            return diff[:max_chars] + "\n\n[diff truncated]"
        return diff

    def raw_diff(
        self,
        max_chars: int = 30_000,
        paths: Iterable[str] | None = None,
    ) -> str:
        return self.full_diff(max_chars=max_chars, paths=paths)

    def review_diff(
        self,
        max_chars: int = 30_000,
        paths: Iterable[str] | None = None,
    ) -> str:
        changes = self.change_set(paths)
        return self.full_diff(max_chars=max_chars, paths=changes.review_files)

    def changed_files_between(self, base_commit: str, head: str = "HEAD") -> list[str]:
        base_revision = self._resolve_commit(base_commit)
        head_revision = self._resolve_commit(head)
        output = self._run(
            [
                "git",
                "diff",
                "--name-only",
                "-z",
                f"{base_revision}..{head_revision}",
                "--",
                ".",
            ]
        )
        return sorted(
            self._normalize_relative_path(path)
            for path in output.split("\0")
            if path
        )

    def commit_diff(
        self,
        base_commit: str,
        head: str = "HEAD",
        *,
        max_chars: int = 30_000,
        paths: Iterable[str] | None = None,
    ) -> str:
        changed = self.changed_files_between(base_commit, head)
        review_files = (
            self.change_set(changed).review_files
            if paths is None
            else self._normalize_paths(paths)
        )
        if not review_files:
            return "No diff."
        base_revision = self._resolve_commit(base_commit)
        head_revision = self._resolve_commit(head)
        diff = self._run(
            [
                "git",
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                f"{base_revision}..{head_revision}",
                "--",
                *review_files,
            ]
        )
        if len(diff) > max_chars:
            return diff[:max_chars] + "\n\n[diff truncated]"
        return diff or "No diff."

    def changed_files(self) -> list[str]:
        return sorted(
            entry.path for entry in self._status_entries() if not entry.ignored
        )

    def status_entries(self) -> list[GitStatusEntry]:
        """Return repository-relative status entries after boundary validation."""
        return list(self._status_entries())

    def ignored_files(self) -> list[str]:
        return sorted(entry.path for entry in self._status_entries() if entry.ignored)

    def all_changed_files(self) -> list[str]:
        return sorted({entry.path for entry in self._status_entries()})

    def relevant_changed_files(
        self,
        paths: Iterable[str] | None = None,
    ) -> list[str]:
        return self.change_set(paths).relevant_files

    def generated_changed_files(
        self,
        paths: Iterable[str] | None = None,
    ) -> list[str]:
        return self.change_set(paths).generated_files

    def change_set(self, paths: Iterable[str] | None = None) -> RepositoryChangeSet:
        entries = self._status_entries()
        entry_by_path = {entry.path: entry for entry in entries}
        selected = (
            self._normalize_paths(paths)
            if paths is not None
            else sorted(entry_by_path)
        )
        tracked = set(self._tracked_files(set(selected)))
        ignored = {
            path
            for path in selected
            if entry_by_path.get(path) is not None and entry_by_path[path].ignored
        }
        generated = {
            path for path in selected if self.artifact_policy.is_generated(path)
        }
        protected = set(self._protected_paths(selected))
        tracked_generated = generated & tracked
        relevant = (
            (set(selected) - ignored - generated - protected) | tracked_generated
        ) - protected
        review_excluded = (generated - tracked_generated) | ignored | protected
        commit_files = set(selected) - ignored - generated - protected
        return RepositoryChangeSet(
            changed_files=sorted(selected),
            relevant_files=sorted(relevant),
            generated_files=sorted(generated),
            ignored_files=sorted(ignored),
            tracked_generated_files=sorted(tracked_generated),
            review_files=sorted(relevant),
            review_excluded_files=sorted(review_excluded),
            commit_files=sorted(commit_files),
            protected_files=sorted(protected),
        )

    def worktree_snapshot(self) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for relative in self.all_changed_files():
            path = self.workspace / relative
            if path.is_file():
                snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.exists():
                snapshot[relative] = "directory"
            else:
                snapshot[relative] = "deleted"
        return snapshot

    def changed_since(self, snapshot: dict[str, str]) -> list[str]:
        current = self.worktree_snapshot()
        return sorted(
            path
            for path in set(snapshot) | set(current)
            if snapshot.get(path) != current.get(path)
        )

    def commit(self, message: str, paths: Iterable[str] | None = None) -> str:
        if not isinstance(message, str) or not message.strip():
            raise GitRepositoryError("Commit message must not be empty.")
        if len(message) > 512 or any(ord(character) < 32 for character in message):
            raise GitRepositoryError(
                "Commit message must be at most 512 characters and single-line."
            )

        requested = self._normalize_paths(paths)
        if paths is not None and not requested:
            raise GitRepositoryError("No relevant changed files are available to commit.")
        selected = self.change_set(requested if paths is not None else None).commit_files
        if not selected:
            raise GitRepositoryError(
                "No relevant changed files are available to commit; generated and "
                "ignored artifacts were skipped."
            )
        pathspec = selected
        self._run(["git", "add", "--all", "--", *pathspec])
        staged_output = self._run(
            ["git", "diff", "--cached", "--name-only", "-z", "--", *pathspec]
        )
        if not any(path for path in staged_output.split("\0") if path):
            raise GitRepositoryError("No staged changes to commit.")

        commit_command = ["git", "commit", "-m", message, "--only", "--", *pathspec]
        self._run(commit_command)
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
        self.validate_workspace()
        return self._run_unvalidated(command)

    def _run_unvalidated(self, command: list[str]) -> str:
        if not command or command[0] != "git" or len(command) < 2:
            raise GitRepositoryError("Only explicit Git argument arrays are supported.")
        operation = command[1]
        identity = self.executable_catalog.resolve("git")
        safe_command = identity.command(
            [
                "--no-pager",
                "--literal-pathspecs",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "commit.gpgSign=false",
                *command[1:],
            ]
        )
        try:
            result = subprocess.run(
                safe_command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
                env=_git_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitRepositoryError(
                f"Git operation '{operation}' timed out after the configured limit."
            ) from exc
        except OSError as exc:
            raise GitRepositoryError(
                f"Git operation '{operation}' could not run: "
                f"{redact_text(type(exc).__name__, max_chars=128)}"
            ) from exc

        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            raise GitRepositoryError(
                f"Git operation '{operation}' failed: "
                f"{redact_text(error, max_chars=2_048)}"
            )
        if len(result.stdout) > self.maximum_command_output_chars:
            raise GitRepositoryError(
                f"Git operation '{operation}' exceeded the bounded output limit."
            )
        return result.stdout.rstrip("\r\n")

    def _status_output(self, *, include_ignored: bool = True) -> str:
        command = [
            "git",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ]
        if include_ignored:
            command.append("--ignored=matching")
        command.extend(["--", "."])
        return self._run(command)

    def _status_entries(self) -> list[GitStatusEntry]:
        fields = self._status_output(include_ignored=True).split("\0")
        entries: list[GitStatusEntry] = []
        index = 0
        while index < len(fields):
            field = fields[index]
            index += 1
            if not field or len(field) < 4:
                continue
            status = field[:2]
            path = self._normalize_relative_path(field[3:])
            entries.append(
                GitStatusEntry(
                    path=path,
                    status=status,
                    tracked=status not in {"??", "!!"},
                    ignored=status == "!!",
                )
            )
            if "R" in status or "C" in status:
                index += 1  # With -z, the following field is the original path.
        return entries

    def _tracked_files(self, paths: set[str]) -> list[str]:
        if not paths:
            return []
        output = self._run(["git", "ls-files", "-z", "--", *sorted(paths)])
        return [path for path in output.split("\0") if path]

    def _untracked_diff(self, relative: str) -> str:
        if self._is_protected_path(relative):
            return f"Protected file content omitted: {relative}"
        path = self.workspace / relative
        if not path.is_file():
            return ""
        if self.artifact_policy.is_generated(relative):
            return f"Generated artifact content omitted: {relative}"
        try:
            with path.open("rb") as handle:
                data = handle.read(30_001)
        except OSError as exc:
            return f"diff unavailable for {relative}: {exc}"
        truncated = len(data) > 30_000
        data = data[:30_000]
        if b"\0" in data:
            return f"diff --git a/{relative} b/{relative}\nBinary file {relative} added"
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        body = "\n".join(f"+{line}" for line in lines)
        if truncated:
            body += "\n+[untracked file content truncated]"
        return (
            f"diff --git a/{relative} b/{relative}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{relative}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n{body}"
        )

    def _normalize_paths(self, paths: Iterable[str] | None) -> list[str]:
        if paths is None:
            return []
        return sorted({self._normalize_relative_path(path) for path in paths})

    def _normalize_relative_path(self, value: str) -> str:
        try:
            normalized = normalize_relative_tool_path(value)
            if normalized.startswith("-"):
                raise GitRepositoryError(
                    "Repository paths cannot begin with an option marker."
                )
            if any(ord(character) < 32 for character in normalized):
                raise GitRepositoryError(
                    "Repository paths cannot contain control characters."
                )
            return self.artifact_policy.normalize(normalized)
        except (ArtifactPolicyError, FileSystemSecurityError) as exc:
            raise GitRepositoryError(str(exc)) from exc

    def _resolve_commit(self, revision: str) -> str:
        self._validate_revision(revision)
        resolved = self._run(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"]
        )
        if not re.fullmatch(r"[a-fA-F0-9]{40,64}", resolved):
            raise GitRepositoryError("Git returned an invalid commit identifier.")
        return resolved.lower()

    @staticmethod
    def _validate_revision(revision: str) -> None:
        if (
            not isinstance(revision, str)
            or not revision
            or len(revision) > 255
            or revision.startswith("-")
            or ".." in revision
            or "@{" in revision
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", revision)
        ):
            raise GitRepositoryError("Git revision contains unsafe syntax.")

    def _protected_paths(self, paths: Iterable[str]) -> list[str]:
        return sorted(path for path in paths if self._is_protected_path(path))

    def _exclude_protected(self, paths: Iterable[str]) -> list[str]:
        return sorted(path for path in paths if not self._is_protected_path(path))

    def _is_protected_path(self, path: str) -> bool:
        if self._path_resolver is None:
            self._path_resolver = ContainedPathResolver(self.workspace)
        return self._path_resolver.classify(path).protected

    def _validate_branch_name(self, branch_name: str) -> None:
        if not branch_name or branch_name.startswith("-"):
            raise GitRepositoryError("Branch name is invalid.")
        if ".." in branch_name or "@{" in branch_name:
            raise GitRepositoryError("Branch name contains unsafe git syntax.")
        if branch_name.endswith("/") or branch_name.endswith("."):
            raise GitRepositoryError("Branch name has an invalid ending.")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch_name):
            raise GitRepositoryError("Branch name contains unsafe characters.")


def _git_environment() -> dict[str, str]:
    environment = safe_child_environment()
    for name in tuple(environment):
        if (
            name.upper().startswith("GIT_")
            and name.upper() not in _GIT_IDENTITY_ENVIRONMENT
        ):
            environment.pop(name, None)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment
