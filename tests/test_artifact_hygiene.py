from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.models import RunStatus
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepository, GitRepositoryError
from agentbus.main import render_execution_report
from agentbus.repo.artifact_policy import (
    ArtifactCategory,
    ArtifactPolicyError,
    GeneratedArtifactPolicy,
)
from agentbus.runtime.orchestrator import MultiAgentOrchestrator
from agentbus.runtime.verifier import Verifier


def init_repository(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    run_git(path, "config", "user.name", "AgentBus Test")
    run_git(path, "config", "user.email", "agentbus@example.invalid")
    return path.resolve()


def run_git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return result.stdout


def test_generated_artifact_policy_is_conservative_and_blocks_traversal():
    policy = GeneratedArtifactPolicy()

    generated = [
        "__pycache__/x.pyc",
        "pkg/module.pyo",
        ".pytest_cache/v/cache/nodeids",
        ".mypy_cache/data.json",
        ".ruff_cache/cache",
        ".coverage",
        "coverage.xml",
        "htmlcov/index.html",
        "node_modules/pkg/index.js",
        "coverage/lcov.info",
        ".next/server.js",
        "dist/app.js",
        "target/classes/App.class",
        "build/output.bin",
        ".gradle/cache.bin",
        ".agentbus/state.db",
        "runs/run.jsonl",
        ".DS_Store",
        "file.py~",
    ]
    for path in generated:
        assert policy.classify(path).category == ArtifactCategory.GENERATED

    assert policy.classify("calculator.py").category == ArtifactCategory.RELEVANT
    assert policy.classify("test_calculator.py").category == ArtifactCategory.RELEVANT
    assert policy.classify("unrelated_notes.txt").category == ArtifactCategory.RELEVANT
    assert policy.classify("ignored.txt", git_ignored=True).category == ArtifactCategory.IGNORED
    with pytest.raises(ArtifactPolicyError):
        policy.classify("../outside.py")


def test_untracked_source_stays_reviewable_while_pycache_is_generated(tmp_path):
    workspace = init_repository(tmp_path / "repo")
    (workspace / "calculator.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (workspace / "test_calculator.py").write_text("def test_add(): pass\n", encoding="utf-8")
    (workspace / "unrelated_notes.txt").write_text("visible\n", encoding="utf-8")
    cache = workspace / "__pycache__" / "x.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"\x00generated-bytecode")

    repository = GitRepository(str(workspace))
    changes = repository.change_set()
    review_diff = repository.review_diff()

    assert changes.relevant_files == [
        "calculator.py",
        "test_calculator.py",
        "unrelated_notes.txt",
    ]
    assert changes.generated_files == ["__pycache__/x.pyc"]
    assert changes.review_excluded_files == ["__pycache__/x.pyc"]
    assert changes.commit_files == changes.relevant_files
    assert "calculator.py" in review_diff
    assert "test_calculator.py" in review_diff
    assert "unrelated_notes.txt" in review_diff
    assert "__pycache__" not in review_diff


def test_gitignore_is_reported_and_env_example_keeps_leading_dot(tmp_path):
    workspace = init_repository(tmp_path / "repo")
    (workspace / ".gitignore").write_text("ignored-output.txt\n", encoding="utf-8")
    (workspace / ".env.example").write_text("SAFE_PLACEHOLDER=\n", encoding="utf-8")
    (workspace / "ignored-output.txt").write_text("ignored\n", encoding="utf-8")

    repository = GitRepository(str(workspace))
    changes = repository.change_set()

    assert ".env.example" in changes.relevant_files
    assert "env.example" not in changes.changed_files
    assert "ignored-output.txt" in changes.ignored_files
    assert "ignored-output.txt" in changes.review_excluded_files
    assert "ignored-output.txt" not in changes.commit_files


def test_tracked_generated_file_is_reviewed_but_not_commit_eligible(tmp_path):
    workspace = init_repository(tmp_path / "repo")
    cache = workspace / "__pycache__" / "tracked.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"\x00baseline")
    run_git(workspace, "add", "__pycache__/tracked.pyc")
    run_git(workspace, "commit", "-m", "track generated fixture")
    cache.write_bytes(b"\x00modified")

    repository = GitRepository(str(workspace))
    changes = repository.change_set()

    assert changes.tracked_generated_files == ["__pycache__/tracked.pyc"]
    assert changes.review_files == ["__pycache__/tracked.pyc"]
    assert "__pycache__/tracked.pyc" not in changes.commit_files
    assert "__pycache__/tracked.pyc" in repository.review_diff()


def test_commit_range_diff_keeps_tracked_generated_files_visible(tmp_path):
    workspace = init_repository(tmp_path / "repo")
    cache = workspace / "__pycache__" / "tracked.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"\x00baseline")
    run_git(workspace, "add", "__pycache__/tracked.pyc")
    run_git(workspace, "commit", "-m", "track generated fixture")
    base = run_git(workspace, "rev-parse", "HEAD").strip()
    cache.write_bytes(b"\x00modified")
    run_git(workspace, "add", "__pycache__/tracked.pyc")
    run_git(workspace, "commit", "-m", "update generated fixture")

    diff = GitRepository(str(workspace)).commit_diff(base)

    assert "__pycache__/tracked.pyc" in diff


def test_path_scoped_commit_skips_generated_artifacts(tmp_path):
    workspace = init_repository(tmp_path / "repo")
    (workspace / "calculator.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    cache = workspace / "__pycache__" / "x.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"\x00generated")
    repository = GitRepository(str(workspace))
    changes = repository.change_set()

    repository.commit("fix: calculator", paths=changes.commit_files)

    committed = run_git(workspace, "show", "--pretty=format:", "--name-only", "HEAD")
    assert committed.strip() == "calculator.py"
    assert cache.exists()
    assert "__pycache__/x.pyc" in repository.changed_files()


def test_commit_refuses_when_only_generated_artifacts_changed(tmp_path):
    workspace = init_repository(tmp_path / "repo")
    cache = workspace / "__pycache__" / "x.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"\x00generated")
    repository = GitRepository(str(workspace))

    with pytest.raises(GitRepositoryError, match="No relevant changed files"):
        repository.commit("fix: generated only")

    assert run_git(workspace, "diff", "--cached", "--name-only") == ""
    assert cache.exists()


def test_explicit_python_verifier_command_is_not_rewritten(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = ["python", "-c", "print('explicit verification')"]

    result = Verifier(
        config=AgentBusConfig(workspace_dir=str(workspace)),
        command=command,
    ).verify()

    assert result["command"] == command
    assert result["artifact_suppression_active"] is True
    assert result["pytest_cache_disabled"] is False


def test_auto_pytest_inherits_environment_without_writing_cache(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = "inherited-marker"
    monkeypatch.setenv("AGENTBUS_INHERITED_MARKER", marker)
    (workspace / "test_environment.py").write_text(
        "import os\n\ndef test_environment():\n"
        f"    assert os.environ['AGENTBUS_INHERITED_MARKER'] == {marker!r}\n",
        encoding="utf-8",
    )

    result = Verifier(
        config=AgentBusConfig(workspace_dir=str(workspace))
    ).verify(require_command=True)

    assert result["passed"] is True
    assert result["artifact_suppression_active"] is True
    assert result["pytest_cache_disabled"] is True
    assert not list(workspace.rglob("*.pyc"))
    assert not (workspace / ".pytest_cache").exists()
    assert os.environ["AGENTBUS_INHERITED_MARKER"] == marker


def test_durable_final_review_ignores_untracked_pycache_but_reports_it(tmp_path):
    workspace = init_repository(tmp_path / "repo")
    settings = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
    )

    class Planner:
        def plan(self, user_task, file_list=None, context_pack=None):
            return {
                "goal": "Create calculator",
                "steps": [
                    {
                        "id": "step-1",
                        "title": "Implement and test",
                        "description": "Create calculator and tests",
                        "risk": "low",
                        "maximum_attempts": 1,
                        "expected_outputs": ["calculator.py", "test_calculator.py"],
                        "done_criteria": ["Tests pass"],
                    }
                ],
                "test_strategy": "Run pytest",
                "done_criteria": ["Final review approves"],
            }

    class Coder:
        def execute(self, user_task, plan, reviewer_feedback=None):
            (workspace / "calculator.py").write_text(
                "def add(a, b): return a + b\n", encoding="utf-8"
            )
            (workspace / "test_calculator.py").write_text(
                "from calculator import add\n\ndef test_add(): assert add(2, 3) == 5\n",
                encoding="utf-8",
            )
            cache = workspace / "__pycache__" / "calculator.pyc"
            cache.parent.mkdir()
            cache.write_bytes(b"\x00generated")
            return "implemented and tested"

    class PassingVerifier:
        def verify(self, require_command=False):
            return {
                "passed": True,
                "command": ["python", "-B", "-m", "pytest"],
                "exit_code": 0,
                "output": "tests passed",
                "reason": "fake offline verifier",
                "artifact_suppression_active": True,
                "pytest_cache_disabled": True,
            }

    class Reviewer:
        def __init__(self):
            self.task_inputs = []
            self.final_inputs = []

        def review_task(self, **kwargs):
            self.task_inputs.append(kwargs)
            return self._result("__pycache__" not in kwargs["task_diff"])

        def review(self, user_task, plan, git_diff, test_output=None, **kwargs):
            self.final_inputs.append({"git_diff": git_diff, **kwargs})
            return self._result("__pycache__" not in git_diff)

        @staticmethod
        def _result(approved):
            return {
                "approved": approved,
                "issues": [],
                "summary": "approved" if approved else "generated artifact leaked",
                "required_fixes": [],
            }

    reviewer = Reviewer()
    store = StateStore(settings.state_database_path)
    runner = MultiAgentOrchestrator(
        config=settings,
        planner=Planner(),
        coder=Coder(),
        verifier=PassingVerifier(),
        reviewer=reviewer,
        state_store=store,
    )

    run_id = runner.create_durable_run("Create a calculator and tests")
    report = runner.run_durable(run_id)

    assert report.status == RunStatus.SUCCEEDED
    assert report.reviewer_status == "approved"
    assert report.relevant_changed_files == ["calculator.py", "test_calculator.py"]
    assert report.generated_artifacts == ["__pycache__/calculator.pyc"]
    assert report.review_excluded_files == ["__pycache__/calculator.pyc"]
    assert report.commit_eligible_files == ["calculator.py", "test_calculator.py"]
    assert report.verifier_artifact_suppression_active is True
    assert reviewer.task_inputs[0]["generated_artifacts"] == [
        "__pycache__/calculator.pyc"
    ]
    assert "__pycache__" not in reviewer.final_inputs[0]["git_diff"]
    assert (workspace / "__pycache__" / "calculator.pyc").exists()
    rendered = render_execution_report(report)
    assert "Generated artifacts detected: __pycache__/calculator.pyc" in rendered
    assert "Files excluded from review: __pycache__/calculator.pyc" in rendered
    assert "Verifier artifact suppression: active" in rendered
