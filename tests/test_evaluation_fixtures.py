from pathlib import Path

import pytest

from agentbus.evaluation.errors import FixtureOwnershipError
from agentbus.evaluation.fixtures import (
    FixtureRepositoryManager,
    FixtureWorkspace,
)
from agentbus.evaluation.models import EvaluationCase
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

    first = manager.create(make_case(source), "run-one")
    second = manager.create(make_case(source), "run-two")

    assert first.repository != second.repository
    assert GitRepository(first.repository).discover_top_level() == first.repository
    assert GitRepository(second.repository).discover_top_level() == second.repository
    (first.repository / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert (second.repository / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (source / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


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
