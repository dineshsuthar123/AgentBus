import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from agentbus.evaluation.errors import FixtureOwnershipError
from agentbus.evaluation.fixtures import (
    FixtureRepositoryManager,
    FixtureWorkspace,
)
from agentbus.evaluation.models import EvaluationCase
from agentbus.evaluation.runner import EvaluationRunner
from agentbus.git.repository import GitRepository


def make_source(path):
    path.mkdir()
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    return path


def make_case(source):
    return EvaluationCase(
        case_id="fixture-case",
        title="Fixture",
        task_prompt="Change app.py",
        fixture_repository_source=str(source),
    )


def test_fixture_manager_creates_fresh_isolated_git_repository_per_run(tmp_path):
    source = make_source(tmp_path / "source")
    manager = FixtureRepositoryManager(tmp_path, tmp_path / "owned")
    first_runner = EvaluationRunner(results_dir=tmp_path / "results-one")
    second_runner = EvaluationRunner(results_dir=tmp_path / "results-two")

    os_temp = Path(tempfile.gettempdir()).resolve()
    expected_owned_root = (os_temp / "agentbus-eval-fixtures").resolve()
    assert Path(tempfile.tempdir).resolve() == os_temp
    assert ".pytest_tmp" not in os_temp.parts
    assert first_runner.fixture_manager.owned_root == expected_owned_root
    assert second_runner.fixture_manager.owned_root == expected_owned_root
    assert os.path.commonpath([expected_owned_root, os_temp]) == str(os_temp)

    first = manager.create(make_case(source), "run-one")
    second = manager.create(make_case(source), "run-two")

    assert first.repository != second.repository
    assert GitRepository(first.repository).discover_top_level() == first.repository
    assert GitRepository(second.repository).discover_top_level() == second.repository
    (first.repository / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert (second.repository / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (source / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_fixture_copy_drops_source_git_metadata_remotes_and_hooks(tmp_path):
    source = make_source(tmp_path / "source")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True, shell=False)
    subprocess.run(
        ["git", "config", "user.name", "Source Owner"],
        cwd=source,
        check=True,
        shell=False,
    )
    subprocess.run(
        ["git", "config", "user.email", "source@example.invalid"],
        cwd=source,
        check=True,
        shell=False,
    )
    subprocess.run(["git", "add", "app.py"], cwd=source, check=True, shell=False)
    subprocess.run(
        ["git", "commit", "-q", "-m", "source baseline"],
        cwd=source,
        check=True,
        shell=False,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.invalid/source.git"],
        cwd=source,
        check=True,
        shell=False,
    )
    (source / ".git" / "hooks" / "pre-commit").write_text(
        "source-only hook\n",
        encoding="utf-8",
    )
    manager = FixtureRepositoryManager(tmp_path, tmp_path / "owned")

    fixture = manager.create(make_case(source), "run-with-git-source")
    remotes = subprocess.run(
        ["git", "remote"],
        cwd=fixture.repository,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    commit_count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=fixture.repository,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )

    assert remotes.stdout.strip() == ""
    assert commit_count.stdout.strip() == "1"
    assert not (fixture.repository / ".git" / "hooks" / "pre-commit").is_file()
    assert (source / ".git").is_dir()


def test_fixture_cleanup_removes_only_owned_marked_tree(tmp_path):
    source = make_source(tmp_path / "source")
    manager = FixtureRepositoryManager(tmp_path, tmp_path / "owned")
    fixture = manager.create(make_case(source), "run")

    manager.cleanup(fixture)

    assert not fixture.owned_root.exists()
    assert source.is_dir()


def test_fixture_cleanup_refuses_missing_or_mismatched_marker(tmp_path):
    source = make_source(tmp_path / "source")
    owned = tmp_path / "owned"
    unknown = owned / "unknown"
    unknown.mkdir(parents=True)
    fixture = FixtureWorkspace(
        evaluation_run_id="run",
        case_id="fixture-case",
        source=source,
        owned_root=unknown,
        repository=unknown / "repo",
        baseline_commit="deadbeef",
    )
    manager = FixtureRepositoryManager(tmp_path, owned)

    with pytest.raises(FixtureOwnershipError, match="ownership marker"):
        manager.cleanup(fixture)

    assert unknown.is_dir()


def test_fixture_source_must_exist(tmp_path):
    manager = FixtureRepositoryManager(tmp_path, tmp_path / "owned")

    with pytest.raises(FixtureOwnershipError, match="does not exist"):
        manager.create(make_case(tmp_path / "missing"), "run")
