from __future__ import annotations

import pytest

from agentbus.agents.planner import PlannerOutput
from agentbus.agents.reviewer import ReviewerOutput
from agentbus.config import AgentBusConfig
from agentbus.control.models import RunCreateRequest
from agentbus.models.deterministic import DeterministicProvider
from agentbus.models.errors import ModelServiceUnavailableError
from agentbus.models.router import ModelRouter, model_request_context
from agentbus.models.types import ModelRole
from agentbus.runtime.schemas import AgentAction


def deterministic_config(**overrides) -> AgentBusConfig:
    values = {
        "provider_name": "deterministic",
        "model_max_retries": 0,
    }
    values.update(overrides)
    return AgentBusConfig(**values)


def test_deterministic_provider_routes_all_roles_through_structured_schemas():
    router = ModelRouter(deterministic_config())

    plan = router.generate_json(
        ModelRole.PLANNER,
        "Plan an offline task.",
        schema=PlannerOutput,
    )
    with model_request_context(run_id="run-1", task_id="step-1"):
        coder = router.generate_json(
            ModelRole.CODER,
            "Return the next action.",
            schema=AgentAction,
        )
        review = router.generate_json(
            ModelRole.REVIEWER,
            "Review the verified task.",
            schema=ReviewerOutput,
        )
    summary = router.generate_text(
        ModelRole.SUMMARIZER,
        "Summarize the deterministic run.",
    )

    assert plan.provider == "deterministic"
    assert plan.json_value()["steps"][0]["id"] == "step-1"
    assert coder.json_value()["action"] == "write_file"
    assert review.json_value()["approved"] is True
    assert "Deterministic summarizer summary" in summary.text_value()


def test_deterministic_coder_sequence_is_scoped_and_repeatable():
    provider = DeterministicProvider(role=ModelRole.CODER)

    first = provider.generate_json(
        "next",
        schema=AgentAction,
        metadata={"run_id": "run-1", "task_id": "step-1"},
    )
    second = provider.generate_json(
        "next",
        schema=AgentAction,
        metadata={"run_id": "run-1", "task_id": "step-1"},
    )
    other_task = provider.generate_json(
        "next",
        schema=AgentAction,
        metadata={"run_id": "run-1", "task_id": "step-2"},
    )

    assert first.request_id == "det-coder-0001"
    assert first.json_value()["path"] == "agentbus_result.py"
    assert second.json_value()["path"] == "test_agentbus_result.py"
    assert other_task.json_value()["path"] == "agentbus_secondary.py"
    assert first.usage.total_tokens == (
        first.usage.input_tokens + first.usage.output_tokens
    )
    assert first.provider_metadata["runtime"] == "offline"


def test_deterministic_latency_and_failure_injection_are_explicit():
    sleeps: list[float] = []
    provider = DeterministicProvider(
        role=ModelRole.REVIEWER,
        latency_seconds=0.25,
        failure_calls=(2,),
        sleeper=sleeps.append,
    )

    result = provider.generate_json("review", schema=ReviewerOutput)
    with pytest.raises(ModelServiceUnavailableError) as captured:
        provider.generate_json("review again", schema=ReviewerOutput)

    assert sleeps == [0.25, 0.25]
    assert result.latency_seconds == 0.25
    assert captured.value.retryable is True
    assert captured.value.metadata == {"deterministic_call": 2}


def test_control_request_allows_offline_provider_without_live_consent(tmp_path):
    request = RunCreateRequest(
        task="Run deterministic acceptance.",
        workspace=str(tmp_path),
        provider="deterministic",
        deterministic={
            "profile": "cancellation-two-task",
            "latency_seconds": 1.5,
            "latency_roles": ["coder"],
        },
    )

    assert request.live_provider_consent is False
    assert request.deterministic.profile == "cancellation-two-task"
    assert request.deterministic.latency_roles == ["coder"]
