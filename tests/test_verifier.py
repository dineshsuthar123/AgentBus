from agentbus.config import AgentBusConfig
from agentbus.runtime.verifier import Verifier


def test_verifier_detects_tests_and_runs_pytest_safely(tmp_path):
    workspace = tmp_path / "workspace"
    tests_dir = workspace / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_sample.py").write_text(
        "def test_sample():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    config = AgentBusConfig(workspace_dir=str(workspace))

    result = Verifier(config=config).verify()

    assert result["command"] == [
        "python",
        "-B",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
    ]
    assert result["exit_code"] == 0
    assert result["passed"] is True
    assert "passed" in result["output"]
    assert result["artifact_suppression_active"] is True
    assert result["pytest_cache_disabled"] is True
    assert not list(workspace.rglob("*.pyc"))
    assert not (workspace / ".pytest_cache").exists()
