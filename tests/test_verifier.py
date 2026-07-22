from agentbus.config import AgentBusConfig
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.runtime.verifier import Verifier
from agentbus.tools.protocol import ToolInvocationStatus
from agentbus.tools.runtime import build_managed_tool_runtime


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


def test_verifier_uses_shared_managed_supervisor_and_audit(tmp_path):
    workspace = tmp_path / "workspace"
    tests_dir = workspace / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_sample.py").write_text(
        "def test_sample():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_dir=str(tmp_path / "state"),
    )
    store = StateStore(config.state_database_path)
    store.create_run_with_tasks(
        RunRecord(
            run_id="run-1",
            original_task="verify",
            model="deterministic",
            workspace=str(workspace.resolve()),
        ),
        [
            TaskSpec(
                task_id="task-1",
                title="Verify",
                description="Run tests",
            )
        ],
    )
    cancellations = CancellationRegistry(store)
    runtime = build_managed_tool_runtime(
        workspace=workspace,
        state_store=store,
        cancellation_registry=cancellations,
    )

    result = Verifier(config=config).verify(
        tool_runtime=runtime,
        run_id="run-1",
        task_id="task-1",
        invocation_key="attempt-1",
    )

    records = store.list_tool_invocations("run-1")
    assert result["passed"] is True
    assert "passed" in result["output"]
    assert len(records) == 1
    assert records[0].tool_name == "test.execute"
    assert records[0].caller_role == "verifier"
    assert records[0].status == ToolInvocationStatus.SUCCEEDED
    assert store.list_tool_audits("run-1")[0].record.invocation_id == (
        records[0].invocation_id
    )
