import json

import pytest
from pydantic import BaseModel, ValidationError

from agentbus.execution.models import (
    AttemptStatus,
    FailureCategory,
    RetryPolicy,
    RunStatus,
    TaskStatus,
)
from agentbus.execution.retry import FailureClassifier, RetryController
from agentbus.execution.transitions import (
    InvalidStateTransition,
    validate_attempt_transition,
    validate_run_transition,
    validate_task_transition,
)
from agentbus.models.errors import (
    ModelAuthenticationError,
    ModelAuthorizationError,
    ModelBadRequestError,
    ModelConfigurationError,
    ModelContentPolicyError,
    ModelNotFoundError,
    ModelQuotaExceededError,
    ModelRateLimitError,
)


def test_valid_transitions_are_accepted():
    validate_run_transition(RunStatus.PENDING, RunStatus.RUNNING)
    validate_task_transition(TaskStatus.PENDING, TaskStatus.READY)
    validate_task_transition(TaskStatus.RUNNING, TaskStatus.SUCCEEDED)
    validate_task_transition(TaskStatus.RUNNING, TaskStatus.WAITING_FOR_APPROVAL)
    validate_attempt_transition(AttemptStatus.RUNNING, AttemptStatus.INTERRUPTED)


def test_invalid_and_terminal_transitions_are_rejected():
    with pytest.raises(InvalidStateTransition, match="pending -> succeeded"):
        validate_task_transition(TaskStatus.PENDING, TaskStatus.SUCCEEDED)
    with pytest.raises(InvalidStateTransition, match="succeeded -> running"):
        validate_task_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING)
    with pytest.raises(InvalidStateTransition, match="succeeded -> retryable"):
        validate_task_transition(TaskStatus.SUCCEEDED, TaskStatus.RETRYABLE)


def test_retry_policy_enforces_category_and_maximum_attempts():
    controller = RetryController(RetryPolicy(maximum_attempts=3))

    retry = controller.decide(
        category=FailureCategory.MODEL_OUTPUT_ERROR,
        attempt_number=1,
        task_maximum_attempts=2,
    )
    exhausted = controller.decide(
        category=FailureCategory.MODEL_OUTPUT_ERROR,
        attempt_number=2,
        task_maximum_attempts=2,
    )
    blocked = controller.decide(
        category=FailureCategory.POLICY_VIOLATION,
        attempt_number=1,
        task_maximum_attempts=3,
    )

    assert retry.should_retry is True
    assert exhausted.should_retry is False
    assert exhausted.exhausted is True
    assert blocked.should_retry is False


def test_retry_delay_metadata_is_deterministic_without_sleeping():
    controller = RetryController(
        RetryPolicy(
            maximum_attempts=5,
            initial_delay_seconds=2,
            delay_multiplier=3,
            maximum_delay_seconds=10,
        )
    )

    assert controller.delay_for(2) == 2
    assert controller.delay_for(3) == 6
    assert controller.delay_for(4) == 10


def test_failure_classifier_distinguishes_retryable_model_output():
    class Value(BaseModel):
        count: int

    with pytest.raises(ValidationError) as captured:
        Value(count="not-an-int")

    classifier = FailureClassifier()
    model_error = classifier.classify(captured.value)
    json_error = classifier.classify(json.JSONDecodeError("bad", "{", 1))
    policy_error = classifier.classify(PermissionError("blocked path"))

    assert model_error.category == FailureCategory.MODEL_OUTPUT_ERROR
    assert model_error.retryable is None
    assert json_error.retryable is None
    assert policy_error.category == FailureCategory.POLICY_VIOLATION
    assert policy_error.retryable is False


@pytest.mark.parametrize(
    "error_type",
    [
        ModelBadRequestError,
        ModelAuthenticationError,
        ModelAuthorizationError,
        ModelConfigurationError,
        ModelNotFoundError,
        ModelQuotaExceededError,
        ModelContentPolicyError,
    ],
)
def test_failure_classifier_reports_nonretryable_provider_request_errors(
    error_type,
):
    error = error_type(
        "Safe provider request failure.",
        provider="azure",
        model="coder-deployment",
        http_status=400,
        request_id="request-safe-1",
        metadata={
            "azure_error_code": "invalid_request",
            "api_key": "must-not-persist",
        },
    )

    classification = FailureClassifier().classify(error)

    assert classification.category == FailureCategory.MODEL_PROVIDER_ERROR
    assert classification.retryable is False
    assert classification.metadata["provider"] == "azure"
    assert classification.metadata["model"] == "coder-deployment"
    assert classification.metadata["request_id"] == "request-safe-1"
    assert classification.metadata["metadata"] == {
        "azure_error_code": "invalid_request",
        "api_key": "[REDACTED]",
    }


def test_failure_classifier_keeps_transient_provider_errors_as_transport():
    classification = FailureClassifier().classify(
        ModelRateLimitError(
            "Rate limited.",
            provider="azure",
            model="coder-deployment",
        )
    )

    assert classification.category == FailureCategory.MODEL_TRANSPORT_ERROR
    assert classification.retryable is True
