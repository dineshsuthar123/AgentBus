import tomllib
from pathlib import Path

from agentbus import __version__
from agentbus.evaluation.models import EvaluationRun, EvaluationVariant
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.schema import SCHEMA_VERSION
from agentbus.execution.state_store import StateStore


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_metadata_version_and_entry_points_are_release_ready():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert project["name"] == "agentbus"
    assert project["dynamic"] == ["version"]
    assert metadata["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "agentbus.__version__"
    }
    assert project["scripts"] == {
        "agentbus": "agentbus.cli:main",
        "agentbus-eval": "agentbus.eval:main",
    }
    assert "openai>=1.66" in project["optional-dependencies"]["azure"]
    assert all("openai" not in dependency for dependency in project["dependencies"])
    assert "pytest>=8" in project["dependencies"]
    assert "pytest>=8" not in project["optional-dependencies"]["dev"]
    assert project["requires-python"] == ">=3.11"


def test_version_is_shared_by_evaluation_and_durable_runtime(tmp_path):
    run = EvaluationRun(
        evaluation_run_id="release-version",
        suite_id="suite",
        variant=EvaluationVariant(variant_id="fake", title="Fake", provider="fake"),
        agentbus_commit_sha="abc",
        configuration_fingerprint="fingerprint",
    )
    assert run.agentbus_version == __version__ == "0.1.0a1"

    store = StateStore(tmp_path / "state.db")
    created = DurableExecutionEngine(store).create_run(
        "Task",
        {
            "goal": "Task",
            "steps": [
                {
                    "id": "step-1",
                    "title": "Step",
                    "description": "Do the step",
                    "risk": "low",
                    "assigned_role": "coder",
                    "maximum_attempts": 1,
                    "expected_outputs": ["result.txt"],
                    "done_criteria": ["result exists"],
                }
            ],
        },
        model="fake",
        workspace=str(tmp_path),
    )
    assert created.metadata["agentbus_version"] == __version__
    assert store.schema_version == SCHEMA_VERSION


def test_package_data_contains_offline_fixtures_and_manifest():
    package_data = ROOT / "agentbus" / "evaluation"
    patterns = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["setuptools"]["package-data"]["agentbus.evaluation"]

    assert (package_data / "real_repositories.json").is_file()
    assert (package_data / "fixtures_data" / "python-feature" / "calculator.py").is_file()
    assert (package_data / "fixtures_data" / "generated-artifact" / ".gitignore").is_file()
    mixed = package_data / "fixtures_data" / "repository-intelligence-mixed"
    assert (mixed / ".env").read_text(encoding="utf-8") == (
        "# Intentionally empty protected-file fixture.\n"
    )
    assert (mixed / ".gitignore").is_file()
    assert (
        mixed / "services" / "python_service" / "broken.py.fixture"
    ).is_file()
    assert "fixtures_data/repository-intelligence-mixed/.env" in patterns
    assert "fixtures_data/repository-intelligence-mixed/.gitignore" in patterns


def test_distribution_manifest_excludes_runtime_artifacts():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    for path in (".agentbus", ".git", ".venv", "build", "dist", "runs"):
        assert f"prune {path}" in manifest
    assert "recursive-exclude * __pycache__ *.py[cod]" in manifest


def test_release_and_community_files_are_present():
    expected = {
        "LICENSE",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "SUPPORT.md",
        "RELEASE_CHECKLIST.md",
    }
    assert expected <= {path.name for path in ROOT.iterdir()}
