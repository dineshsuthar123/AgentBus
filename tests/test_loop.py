import json

import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.state_store import StateStore
from agentbus.models.errors import ModelOutputError
from agentbus.runtime.loop import AgentLoop, ManagedToolApprovalRequired
from agentbus.tools.protocol import ToolInvocationStatus


class RecoveringModel:
    def __init__(self):
        self.calls = 0

    def generate_json(self, prompt):
        self.calls += 1

        if self.calls == 1:
            raise ValueError("bad model output")

        assert "bad model output" in prompt
        return {"action": "finish", "summary": "recovered"}


def test_loop_recovers_from_model_error(tmp_path):
    config = AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=2,
    )
    loop = AgentLoop(config=config)
    loop.model = RecoveringModel()

    result = loop.run("finish after recovering")

    assert result == "recovered"

    log_file = next((tmp_path / "runs").glob("*.jsonl"))
    events = [
        json.loads(line)["type"]
        for line in log_file.read_text(encoding="utf-8").splitlines()
    ]

    assert "model_error" in events
    assert "run_finished" in events


def test_loop_stops_at_max_steps(tmp_path):
    class NeverFinishes:
        def generate_json(self, prompt):
            return {
                "action": "tool_call",
                "tool_call": {
                    "tool_name": "filesystem.list",
                    "arguments": {},
                    "expected_capabilities": ["filesystem.read"],
                    "idempotency_key": "list-workspace",
                },
            }

    config = AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=1,
    )
    loop = AgentLoop(config=config)
    loop.model = NeverFinishes()

    result = loop.run("list files forever")

    assert "max_steps was reached" in result


def test_loop_recovers_from_normalized_model_output_error(tmp_path):
    class NormalizedRecoveringModel:
        def __init__(self):
            self.calls = 0

        def generate_json(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ModelOutputError(
                    "malformed structured action",
                    provider="ollama",
                    model="local-model",
                )
            return {"action": "finish", "summary": "normalized recovery"}

    config = AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=2,
    )
    model = NormalizedRecoveringModel()
    loop = AgentLoop(config=config, model=model)

    assert loop.run("recover") == "normalized recovery"
    assert model.calls == 2


def test_loop_routes_structured_model_call_through_policy_and_audit(tmp_path):
    class WritingModel:
        def __init__(self):
            self.calls = 0

        def generate_json(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                assert "filesystem.write" in prompt
                return {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "filesystem.write",
                        "arguments": {
                            "path": "result.py",
                            "content": "VALUE = 3\n",
                        },
                        "expected_capabilities": [
                            "filesystem.write",
                            "filesystem.create",
                        ],
                        "idempotency_key": "create-result",
                    },
                }
            assert '"status": "succeeded"' in prompt
            return {"action": "finish", "summary": "managed write complete"}

    workspace = tmp_path / "workspace"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=2,
    )
    loop = AgentLoop(config=config, model=WritingModel())

    assert loop.run("create result") == "managed write complete"
    assert (workspace / "result.py").read_text(encoding="utf-8") == "VALUE = 3\n"

    store = StateStore(config.state_database_path)
    records = store.list_tool_invocations(loop.run_id)
    audits = store.list_tool_audits(loop.run_id)
    assert len(records) == 1
    assert records[0].status == ToolInvocationStatus.SUCCEEDED
    assert records[0].caller_role == "coder"
    assert records[0].workspace_identity == str(workspace.resolve())
    assert len(audits) == 1
    assert audits[0].record.invocation_id == records[0].invocation_id


def test_loop_rejects_incorrect_model_capability_claim_before_dispatch(tmp_path):
    class MismatchedModel:
        def __init__(self):
            self.calls = 0

        def generate_json(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "filesystem.write",
                        "arguments": {"path": "unsafe.py", "content": "bad\n"},
                        "expected_capabilities": ["filesystem.read"],
                        "idempotency_key": "wrong-capability",
                    },
                }
            assert "do not exactly match runtime derivation" in prompt
            return {"action": "finish", "summary": "rejected safely"}

    workspace = tmp_path / "workspace"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=2,
    )
    loop = AgentLoop(config=config, model=MismatchedModel())

    assert loop.run("reject mismatch") == "rejected safely"
    assert not (workspace / "unsafe.py").exists()
    assert StateStore(config.state_database_path).list_tool_invocations(
        loop.run_id
    ) == []


def test_loop_suspends_for_exact_managed_tool_approval(tmp_path):
    class SensitiveWriteModel:
        def generate_json(self, prompt, **kwargs):
            return {
                "action": "tool_call",
                "tool_call": {
                    "tool_name": "filesystem.write",
                    "arguments": {
                        "path": ".github/workflows/ci.yml",
                        "content": "name: checks\n",
                    },
                    "expected_capabilities": [
                        "filesystem.write",
                        "filesystem.create",
                    ],
                    "idempotency_key": "write-ci",
                },
            }

    workspace = tmp_path / "workspace"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=1,
    )
    loop = AgentLoop(config=config, model=SensitiveWriteModel())

    with pytest.raises(ManagedToolApprovalRequired) as captured:
        loop.run("change CI")

    store = StateStore(config.state_database_path)
    approval = store.get_tool_approval(
        loop.run_id,
        captured.value.approval_id,
    )
    assert approval.disposition is None
    assert approval.request.invocation_id == captured.value.invocation_id
    assert store.get_run(loop.run_id).status.value == "waiting_for_approval"
    assert not (workspace / ".github/workflows/ci.yml").exists()
