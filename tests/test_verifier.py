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

    assert result["command"] == ["python", "-m", "pytest"]
    assert result["exit_code"] == 0
    assert result["passed"] is True
    assert "passed" in result["output"]
