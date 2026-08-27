import json
from types import SimpleNamespace

import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.state_store import StateStore
from agentbus.models.errors import ModelBadRequestError, ModelOutputError
from agentbus.product.logging import read_product_logs
from agentbus.runtime.loop import (
    AgentLoop,
    ManagedToolApprovalRequired,
    ManagedToolContinuationError,
    PlannedCapabilityMismatchError,
)
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


def test_durable_loop_provider_error_is_discoverable_by_run_id(tmp_path):
    run_id = "durable-provider-run"
    workspace = tmp_path / "workspace"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=1,
    )

    class FailingProviderModel:
        def generate_json(self, prompt, **kwargs):
            raise ModelBadRequestError(
                "Azure OpenAI rejected the request as invalid.",
                provider="azure",
                model="coder-deployment",
                http_status=400,
                request_id="request-safe-1",
                metadata={
                    "azure_error_code": "invalid_request_error",
                    "azure_error_param": "text.format.schema",
                },
            )

    tool_runtime = SimpleNamespace(
        worktree=workspace.resolve(),
        cancellations=SimpleNamespace(get=lambda selected_run_id: None),
        registry=SimpleNamespace(descriptors=lambda: ()),
    )
    loop = AgentLoop(
        config=config,
        model=FailingProviderModel(),
        tool_runtime=tool_runtime,
        run_id=run_id,
        task_id="step-1",
    )

    with pytest.raises(ModelBadRequestError):
        loop.run("fail safely")

    entries = read_product_logs(config, run_id=run_id)
    assert loop.logger.run_id == run_id
    assert loop.logger.log_file.name.endswith(f"_{run_id}.jsonl")
    assert entries
    assert {entry.run_id for entry in entries} == {run_id}
    assert any(
        "model_error" in entry.message
        and "invalid_request_error" in entry.message
        for entry in entries
    )


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


def test_final_action_observation_gets_one_terminal_consumption_turn(tmp_path):
    class FinishAfterObservation:
        def __init__(self):
            self.calls = 0

        def generate_json(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "filesystem.write",
                        "arguments": {
                            "path": "result.py",
                            "content": "VALUE = 1\n",
                        },
                        "expected_capabilities": [
                            "filesystem.write",
                            "filesystem.create",
                        ],
                        "idempotency_key": "final-step-write",
                    },
                }
            assert '"status": "succeeded"' in prompt
            assert "terminal decision" in prompt.lower()
            return {"action": "finish", "summary": "consumed final observation"}

    workspace = tmp_path / "workspace"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=1,
    )
    model = FinishAfterObservation()
    loop = AgentLoop(config=config, model=model)

    assert loop.run("write once and finish") == "consumed final observation"
    assert model.calls == 2
    assert (workspace / "result.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_terminal_consumption_turn_cannot_dispatch_another_tool(tmp_path):
    class RequestsBeyondBudget:
        def __init__(self):
            self.calls = 0

        def generate_json(self, prompt, **kwargs):
            self.calls += 1
            target = "first.py" if self.calls == 1 else "must-not-exist.py"
            return {
                "action": "tool_call",
                "tool_call": {
                    "tool_name": "filesystem.write",
                    "arguments": {"path": target, "content": "VALUE = 1\n"},
                    "expected_capabilities": [
                        "filesystem.write",
                        "filesystem.create",
                    ],
                    "idempotency_key": f"write-{self.calls}",
                },
            }

    workspace = tmp_path / "workspace"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=1,
    )
    model = RequestsBeyondBudget()
    loop = AgentLoop(config=config, model=model)

    with pytest.raises(Exception, match="step_budget_exhausted"):
        loop.run("do not exceed the action budget")

    assert model.calls == 2
    assert (workspace / "first.py").exists()
    assert not (workspace / "must-not-exist.py").exists()


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
    assert loop.tool_runtime is None
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


def test_loop_refuses_to_persist_sensitive_approval_continuation(tmp_path):
    secret = "approval-continuation-secret-value"

    class SensitiveActionModel:
        def generate_json(self, prompt, **kwargs):
            return {
                "action": "tool_call",
                "tool_call": {
                    "tool_name": "filesystem.write",
                    "arguments": {
                        "path": ".github/workflows/ci.yml",
                        "content": f"api_key={secret}\n",
                    },
                    "expected_capabilities": [
                        "filesystem.write",
                        "filesystem.create",
                    ],
                    "idempotency_key": "sensitive-ci-write",
                },
            }

    workspace = tmp_path / "workspace"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=1,
    )
    loop = AgentLoop(config=config, model=SensitiveActionModel())

    with pytest.raises(
        ManagedToolContinuationError,
        match="cannot be persisted safely",
    ):
        loop.run("change CI without persisting secrets")

    store = StateStore(config.state_database_path)
    assert store.get_run(loop.run_id).status.value == "failed"
    assert not (workspace / ".github/workflows/ci.yml").exists()
    persisted = b"".join(
        path.read_bytes()
        for root in (tmp_path / "state", tmp_path / "runs")
        if root.exists()
        for path in root.rglob("*")
        if path.is_file()
    )
    assert secret.encode("utf-8") not in persisted


def test_loop_classifies_malformed_persisted_continuation_as_resumability_failure(
    tmp_path,
):
    class UnexpectedModel:
        def generate_json(self, prompt, **kwargs):
            pytest.fail("a malformed continuation must fail before calling the model")

    config = AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=1,
    )
    loop = AgentLoop(config=config, model=UnexpectedModel())

    with pytest.raises(
        ManagedToolContinuationError,
        match="invalid or incomplete",
    ) as raised:
        loop.run("refuse malformed state", continuation={"schema_version": 1})

    assert raised.value.category.value == "resumability_failure"
    assert raised.value.retryable is False
    assert StateStore(config.state_database_path).get_run(loop.run_id).status.value == (
        "failed"
    )


def test_loop_rejects_capability_outside_planner_requirements(tmp_path):
    class OverreachingModel:
        def __init__(self):
            self.calls = 0

        def generate_json(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "filesystem.write",
                        "arguments": {"path": "result.py", "content": "VALUE = 1\n"},
                        "expected_capabilities": [
                            "filesystem.write",
                            "filesystem.create",
                        ],
                        "idempotency_key": "planner-overreach",
                    },
                }
            raise AssertionError("planner mismatch must stop before another model call")

    workspace = tmp_path / "workspace"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=2,
    )
    loop = AgentLoop(
        config=config,
        model=OverreachingModel(),
        policy_context={"planned_capabilities": ["filesystem.read"]},
    )

    with pytest.raises(PlannedCapabilityMismatchError) as captured:
        loop.run("respect plan")

    assert captured.value.task_id == "single-task"
    assert captured.value.tool_name == "filesystem.write"
    assert captured.value.requested_capabilities == [
        "filesystem.create",
        "filesystem.write",
    ]
    assert captured.value.declared_capabilities == ["filesystem.read"]
    assert captured.value.undeclared_capabilities == [
        "filesystem.create",
        "filesystem.write",
    ]
    assert loop.model.calls == 1
    assert not (workspace / "result.py").exists()
    events = [
        json.loads(line)
        for line in loop.logger.log_file.read_text(encoding="utf-8").splitlines()
    ]
    mismatch = next(
        event for event in events if event["type"] == "plan_capability_mismatch"
    )
    assert mismatch["data"] == {
        "step": 1,
        "task_id": "single-task",
        "tool_name": "filesystem.write",
        "requested_capabilities": [
            "filesystem.create",
            "filesystem.write",
        ],
        "declared_capabilities": ["filesystem.read"],
        "undeclared_capabilities": [
            "filesystem.create",
            "filesystem.write",
        ],
    }
    assert "arguments" not in mismatch["data"]
    store = StateStore(config.state_database_path)
    assert store.get_run(loop.run_id).status.value == "failed"


def test_planner_authorized_missing_write_derives_create_and_proceeds(tmp_path):
    class AuthorizedCreateModel:
        def __init__(self):
            self.calls = 0

        def generate_json(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "filesystem.write",
                        "arguments": {
                            "path": "result.py",
                            "content": "VALUE = 1\n",
                        },
                        "expected_capabilities": [
                            "filesystem.write",
                            "filesystem.create",
                        ],
                        "idempotency_key": "authorized-create",
                    },
                }
            assert '"status": "succeeded"' in prompt
            return {"action": "finish", "summary": "created authorized file"}

    workspace = tmp_path / "workspace"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=2,
    )
    model = AuthorizedCreateModel()
    loop = AgentLoop(
        config=config,
        model=model,
        policy_context={
            "planned_capabilities": [
                "filesystem.write",
                "filesystem.create",
            ]
        },
    )

    result = loop.run("create the explicitly authorized result")

    assert result == "created authorized file"
    assert (workspace / "result.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    invocations = StateStore(config.state_database_path).list_tool_invocations(
        loop.run_id
    )
    assert len(invocations) == 1
    assert {
        capability.name.value
        for capability in invocations[0].capabilities
    } == {"filesystem.create", "filesystem.write"}


def test_explicit_empty_planner_capability_set_denies_tool_calls(tmp_path):
    class ReadModel:
        def generate_json(self, prompt, **kwargs):
            return {
                "action": "tool_call",
                "tool_call": {
                    "tool_name": "filesystem.list",
                    "arguments": {},
                    "expected_capabilities": ["filesystem.read"],
                    "idempotency_key": "empty-upper-bound",
                },
            }

    config = AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=1,
    )
    loop = AgentLoop(
        config=config,
        model=ReadModel(),
        policy_context={"planned_capabilities": []},
    )

    with pytest.raises(PlannedCapabilityMismatchError) as captured:
        loop.run("respect empty capability contract")

    assert captured.value.requested_capabilities == ["filesystem.read"]
    assert captured.value.declared_capabilities == []


def test_planned_write_capability_cannot_escape_managed_workspace(tmp_path):
    outside = tmp_path / "outside.py"

    class EscapingModel:
        def __init__(self):
            self.calls = 0

        def generate_json(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "filesystem.write",
                        "arguments": {
                            "path": str(outside),
                            "content": "SHOULD_NOT_EXIST = True\n",
                        },
                        "expected_capabilities": [
                            "filesystem.write",
                            "filesystem.create",
                        ],
                        "idempotency_key": "outside-planned-scope",
                    },
                }
            assert '"status": "denied"' in prompt
            return {
                "action": "finish",
                "summary": "workspace boundary rejected the write",
            }

    workspace = tmp_path / "workspace"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=2,
    )
    model = EscapingModel()
    loop = AgentLoop(
        config=config,
        model=model,
        policy_context={
            "planned_capabilities": [
                "filesystem.write",
                "filesystem.create",
            ]
        },
    )

    result = loop.run("write only inside the managed workspace")

    assert result == "workspace boundary rejected the write"
    assert model.calls == 2
    assert outside.exists() is False
    invocations = StateStore(config.state_database_path).list_tool_invocations(
        loop.run_id
    )
    assert len(invocations) == 1
    assert invocations[0].status == ToolInvocationStatus.DENIED
    event_types = {
        json.loads(line)["type"]
        for line in loop.logger.log_file.read_text(encoding="utf-8").splitlines()
    }
    assert "plan_capability_mismatch" not in event_types
