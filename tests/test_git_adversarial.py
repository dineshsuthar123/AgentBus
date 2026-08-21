from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

import agentbus.git.repository as repository_module
from agentbus.git.repository import GitRepository, GitRepositoryError


def run_git(
    workspace: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        check=check,
        capture_output=True,
        text=True,
        shell=False,
        env=environment,
    )


def initialized_repository(path: Path) -> Path:
    path.mkdir(parents=True)
    run_git(path, "init", "-q")
    run_git(path, "config", "user.name", "AgentBus Security Test")
    run_git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "README.md").write_text("baseline\n", encoding="utf-8")
    run_git(path, "add", "--", "README.md")
    run_git(path, "commit", "-q", "-m", "test: baseline")
    return path.resolve()


def metadata_path(workspace: Path, name: str) -> Path:
    value = run_git(workspace, "rev-parse", "--git-path", name).stdout.strip()
    candidate = Path(value)
    return candidate if candidate.is_absolute() else workspace / candidate


def test_managed_git_uses_explicit_argv_and_option_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    filename = "name;not-a-command.txt"
    (workspace / filename).write_text("safe\n", encoding="utf-8")
    original_run = subprocess.run
    calls: list[tuple[list[str], dict[str, object]]] = []

    def recording_run(
        command: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(repository_module.subprocess, "run", recording_run)

    staged = GitRepository(str(workspace)).stage([filename])

    assert staged == [filename]
    relevant = [(command, kwargs) for command, kwargs in calls if filename in command]
    assert relevant
    for command, kwargs in relevant:
        assert isinstance(command, list)
        assert Path(command[0]).is_absolute()
        assert "--literal-pathspecs" in command
        assert command.index("--") < command.index(filename)
        assert kwargs["shell"] is False
    assert not any(
        operation in command
        for command, _ in calls
        for operation in ("push", "fetch", "pull")
    )


def test_option_like_filename_fails_closed(tmp_path: Path) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    (workspace / "-n").write_text("option-like\n", encoding="utf-8")
    repository = GitRepository(str(workspace))

    with pytest.raises(GitRepositoryError, match="option marker") as captured:
        repository.changed_files()

    assert len(str(captured.value)) < 256


@pytest.mark.parametrize(
    "path",
    (
        ":(glob)**",
        ":!README.md",
        "../outside.txt",
        "name.txt:alternate",
        "line\nbreak.txt",
    ),
)
def test_ambiguous_pathspecs_are_rejected_before_git(
    tmp_path: Path,
    path: str,
) -> None:
    workspace = initialized_repository(tmp_path / "repository")

    with pytest.raises(GitRepositoryError):
        GitRepository(str(workspace)).full_diff(paths=[path])


@pytest.mark.parametrize(
    "revision",
    (
        "-n",
        "HEAD..side",
        "HEAD@{1}",
        "HEAD^{tree}",
        ":(glob)**",
    ),
)
def test_revision_and_path_ambiguity_is_rejected(
    tmp_path: Path,
    revision: str,
) -> None:
    workspace = initialized_repository(tmp_path / "repository")

    with pytest.raises(GitRepositoryError, match="unsafe syntax") as captured:
        GitRepository(str(workspace)).show_commit(revision)

    assert len(str(captured.value)) < 256


def test_unusual_valid_branch_is_supported_and_unsafe_names_fail_closed(
    tmp_path: Path,
) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    repository = GitRepository(str(workspace))

    repository.create_branch("feature/slash_name-1")

    assert repository.current_branch() == "feature/slash_name-1"
    for branch in (
        "-unsafe",
        "feature..side",
        "feature@{1}",
        "space name",
        "ending.",
        "ending/",
    ):
        with pytest.raises(GitRepositoryError) as captured:
            repository.create_branch(branch)
        assert len(str(captured.value)) < 2_200


def test_hooks_and_repository_external_diff_are_never_executed(
    tmp_path: Path,
) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    hook_marker = tmp_path / "hook-ran.txt"
    diff_marker = tmp_path / "external-diff-ran.txt"
    hook = metadata_path(workspace, "hooks") / "pre-commit"
    external_diff = tmp_path / "external-diff.sh"
    hook.write_text(
        "#!/bin/sh\n"
        f"printf executed > {shlex.quote(hook_marker.as_posix())}\n"
        "exit 1\n",
        encoding="utf-8",
    )
    external_diff.write_text(
        "#!/bin/sh\n"
        f"printf executed > {shlex.quote(diff_marker.as_posix())}\n"
        "exit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    external_diff.chmod(0o755)
    run_git(
        workspace,
        "config",
        "diff.external",
        shlex.quote(external_diff.as_posix()),
    )
    (workspace / "README.md").write_text("managed update\n", encoding="utf-8")
    repository = GitRepository(str(workspace))

    diff = repository.review_diff(paths=["README.md"])
    repository.commit("test: managed update", paths=["README.md"])

    assert "managed update" in diff
    assert hook_marker.exists() is False
    assert diff_marker.exists() is False


def test_managed_operations_do_not_mutate_global_config_or_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    home = tmp_path / "isolated-home"
    home.mkdir()
    global_config = home / ".gitconfig"
    global_config.write_text(
        "[alias]\n"
        "    agentbus-test = status\n"
        "[user]\n"
        "    name = Must Remain Untouched\n",
        encoding="utf-8",
    )
    original_config = global_config.read_bytes()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    remote = tmp_path / "remote.git"
    remote.mkdir()
    run_git(remote, "init", "--bare", "-q")
    run_git(workspace, "remote", "add", "origin", str(remote))
    remote_before = run_git(remote, "show-ref", check=False).stdout
    (workspace / "local-only.txt").write_text("local\n", encoding="utf-8")
    repository = GitRepository(str(workspace))

    repository.commit("test: local-only mutation", paths=["local-only.txt"])

    assert repository.remote_url() == str(remote)
    assert global_config.read_bytes() == original_config
    assert run_git(remote, "show-ref", check=False).stdout == remote_before


def test_detached_head_operations_remain_local_and_bounded(tmp_path: Path) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    branch = run_git(workspace, "branch", "--show-current").stdout.strip()
    base = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
    run_git(workspace, "checkout", "--detach", "-q")
    (workspace / "detached.txt").write_text("detached\n", encoding="utf-8")
    repository = GitRepository(str(workspace))

    status = repository.bounded_status(max_chars=512)
    repository.commit("test: detached commit", paths=["detached.txt"])

    assert "detached.txt" in status
    assert len(status) <= 512
    assert repository.current_branch() == ""
    assert repository.head_commit(short=False) != base
    branch_head = run_git(
        workspace,
        "rev-parse",
        f"refs/heads/{branch}",
    ).stdout.strip()
    assert branch_head == base


def test_stale_worktree_metadata_does_not_break_repository_reads(
    tmp_path: Path,
) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    stale = tmp_path / "stale-worktree"
    base = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
    run_git(
        workspace,
        "worktree",
        "add",
        "-q",
        "-b",
        "stale-worktree-test",
        str(stale),
    )
    shutil.rmtree(stale)
    repository = GitRepository(str(workspace))

    status = repository.bounded_status(max_chars=512)
    branches = repository.branches(max_chars=512)

    assert len(status) <= 512
    assert "stale-worktree-test" in branches
    assert repository.head_commit(short=False) == base
    assert stale.exists() is False


def test_merge_conflict_unmerged_index_and_incomplete_operation_are_safe(
    tmp_path: Path,
) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    main_branch = run_git(workspace, "branch", "--show-current").stdout.strip()
    run_git(workspace, "switch", "-q", "-c", "conflicting-side")
    (workspace / "README.md").write_text("side\n", encoding="utf-8")
    run_git(workspace, "add", "--", "README.md")
    run_git(workspace, "commit", "-q", "-m", "test: side")
    run_git(workspace, "switch", "-q", main_branch)
    (workspace / "README.md").write_text("main\n", encoding="utf-8")
    run_git(workspace, "add", "--", "README.md")
    run_git(workspace, "commit", "-q", "-m", "test: main")

    merge = run_git(
        workspace,
        "merge",
        "--no-edit",
        "conflicting-side",
        check=False,
    )
    repository = GitRepository(str(workspace))
    status = repository.bounded_status(max_chars=1_024)
    unmerged = run_git(workspace, "ls-files", "-u").stdout

    assert merge.returncode != 0
    assert "README.md" in repository.changed_files()
    assert "README.md" in status
    assert len(status) <= 1_024
    assert unmerged
    assert metadata_path(workspace, "MERGE_HEAD").is_file()


def test_malformed_git_metadata_produces_a_bounded_safe_error(
    tmp_path: Path,
) -> None:
    workspace = initialized_repository(tmp_path / "repository")
    secret = "must-not-reach-diagnostics"
    index = metadata_path(workspace, "index")
    index.write_bytes(f"API_KEY={secret}".encode("utf-8"))

    with pytest.raises(GitRepositoryError) as captured:
        GitRepository(str(workspace)).bounded_status(max_chars=512)

    diagnostic = str(captured.value)
    assert secret not in diagnostic
    assert len(diagnostic) <= 2_200
