from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentbus.control.models import (
    API_PREFIX,
    CONTROL_PROTOCOL_VERSION,
    ErrorBody,
    ErrorResponse,
    RunCreateRequest,
    ToolPolicyEvaluationRequest,
    WorkflowMode,
)


def test_protocol_version_and_prefix_are_stable() -> None:
    assert CONTROL_PROTOCOL_VERSION == "1.0"
    assert API_PREFIX == "/api/v1"


def test_run_request_accepts_parallel_durable_multi_agent_mode() -> None:
    request = RunCreateRequest(
        task="Implement a bounded API.",
        workspace="C:/workspace",
        workflow=WorkflowMode.MULTI,
        durable=True,
        parallel=True,
        max_workers=4,
    )

    assert request.parallel is True
    assert request.max_workers == 4


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"parallel": True}, "parallel execution requires"),
        ({"max_workers": 2}, "max_workers greater than one"),
        ({"create_pr": True}, "PR creation requires"),
        ({"fallback_enabled": True}, "requires a fallback_provider"),
        (
            {"provider": "azure"},
            "Azure execution requires explicit live_provider_consent",
        ),
    ],
)
def test_run_request_rejects_unsafe_mode_combinations(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        RunCreateRequest(
            task="Task",
            workspace="C:/workspace",
            **overrides,
        )


def test_run_request_rejects_unknown_transport_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RunCreateRequest(
            task="Task",
            workspace="C:/workspace",
            api_key="must-not-cross-the-control-protocol",
        )


def test_run_request_accepts_a_validated_tool_resource_budget() -> None:
    request = RunCreateRequest(
        task="Run one bounded tool.",
        workspace="C:/workspace",
        provider="deterministic",
        deterministic={"profile": "tool-budget-exhaustion"},
        tool_budget={
            "invocations_per_task": 1,
            "invocations_per_run": 1,
            "stdout_bytes": 1024,
            "stderr_bytes": 1024,
            "combined_output_bytes": 2048,
        },
    )

    assert request.tool_budget.invocations_per_task == 1
    assert request.tool_budget.invocations_per_run == 1
    assert request.tool_budget.combined_output_bytes == 2048


@pytest.mark.parametrize(
    "arguments",
    [
        {"value": float("nan")},
        {"value": "x" * 65_536},
    ],
)
def test_tool_policy_diagnostics_reject_unbounded_or_nonfinite_arguments(
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="finite JSON|at most 65536 bytes"):
        ToolPolicyEvaluationRequest(
            run_id="run-1",
            task_id="task-1",
            tool_name="filesystem.read",
            arguments=arguments,
        )


def test_error_response_has_stable_safe_shape() -> None:
    response = ErrorResponse(
        error=ErrorBody(
            code="invalid_workspace",
            message="The selected workspace is invalid.",
        )
    )

    assert response.model_dump(mode="json") == {
        "error": {
            "code": "invalid_workspace",
            "message": "The selected workspace is invalid.",
            "retryable": False,
            "details": {},
        }
    }
