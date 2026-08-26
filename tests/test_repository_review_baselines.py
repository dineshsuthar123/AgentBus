import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from agentbus.git.repository import GitRepository, RepositoryBaselineMismatch


def _git(workspace: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        capture_output=True,
        check=True,
        shell=False,
        text=True,
    )
    return completed.stdout.strip()


def _repository(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "AgentBus Tests")
    _git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "foo.txt").write_text("old\n", encoding="utf-8")
    (path / "bar.txt").write_text("old bar\n", encoding="utf-8")
    _git(path, "add", "foo.txt", "bar.txt")
    _git(path, "commit", "-q", "-m", "baseline")
    return path


def test_cumulative_review_diff_is_original_to_final_and_leaves_index_untouched(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "repo")
    repository = GitRepository(str(workspace))
    index_path = workspace / ".git" / "index"
    index_before = index_path.read_bytes()
    cached_before = _git(workspace, "diff", "--cached")
    baseline = repository.capture_review_baseline()

    (workspace / "foo.txt").write_text("intermediate\n", encoding="utf-8")
    attempt_two = repository.capture_review_baseline()
    (workspace / "foo.txt").write_text("final\n", encoding="utf-8")

    candidate = repository.review_candidate(baseline)
    cumulative = repository.review_diff_since_baseline(
        baseline,
        paths=["foo.txt"],
        candidate=candidate,
    )
    attempt_local = repository.review_diff_since_baseline(
        attempt_two,
        paths=["foo.txt"],
    )

    assert "-old" in cumulative
    assert "+final" in cumulative
    assert "intermediate" not in cumulative
    assert "-intermediate" in attempt_local
    assert "+final" in attempt_local
    assert repository.changed_files_since_review_baseline(baseline) == ["foo.txt"]
    assert repository.changed_files_since_review_baseline(attempt_two) == ["foo.txt"]
    assert index_path.read_bytes() == index_before
    assert _git(workspace, "diff", "--cached") == cached_before


def test_task_and_attempt_baselines_distinguish_retained_changes(tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "repo")
    repository = GitRepository(str(workspace))
    task_baseline = repository.capture_review_baseline()
    (workspace / "foo.txt").write_text("attempt one\n", encoding="utf-8")
    attempt_two_baseline = repository.capture_review_baseline()
    (workspace / "bar.txt").write_text("attempt two\n", encoding="utf-8")

    assert repository.changed_files_since_review_baseline(task_baseline) == [
        "bar.txt",
        "foo.txt",
    ]
    assert repository.changed_files_since_review_baseline(attempt_two_baseline) == [
        "bar.txt"
    ]
    cumulative = repository.review_diff_since_baseline(task_baseline)
    assert "attempt one" in cumulative
    assert "attempt two" in cumulative


def test_explicit_restore_to_task_baseline_produces_no_cumulative_diff(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "repo")
    repository = GitRepository(str(workspace))
    task_baseline = repository.capture_review_baseline()
    (workspace / "foo.txt").write_text("rejected change\n", encoding="utf-8")

    # This models an explicit supported restore without asking AgentBus to destroy data.
    (workspace / "foo.txt").write_text("old\n", encoding="utf-8")
    retry_baseline = repository.capture_review_baseline()

    assert retry_baseline["tree_id"] == task_baseline["tree_id"]
    assert repository.changed_files_since_review_baseline(task_baseline) == []
    assert repository.review_diff_since_baseline(task_baseline) == "No diff."


def test_cumulative_review_rejects_head_drift(tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "repo")
    repository = GitRepository(str(workspace))
    baseline = repository.capture_review_baseline()
    (workspace / "later.txt").write_text("committed elsewhere\n", encoding="utf-8")
    _git(workspace, "add", "later.txt")
    _git(workspace, "commit", "-q", "-m", "move head")

    with pytest.raises(RepositoryBaselineMismatch, match="HEAD changed"):
        repository.review_candidate(baseline)


@pytest.mark.parametrize("field", ["head_commit", "tree_id"])
def test_cumulative_review_rejects_checksum_consistent_invalid_revisions(
    tmp_path: Path,
    field: str,
) -> None:
    workspace = _repository(tmp_path / "repo")
    repository = GitRepository(str(workspace))
    baseline = repository.capture_review_baseline()
    baseline[field] = "--reset"
    payload = {key: value for key, value in baseline.items() if key != "identity_sha256"}
    baseline["identity_sha256"] = hashlib.sha256(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(RepositoryBaselineMismatch, match="malformed"):
        repository.review_candidate(baseline)
