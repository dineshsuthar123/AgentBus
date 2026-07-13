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


def test_valid_transitions_are_accepted():
    validate_run_transition(RunStatus.PENDING, RunStatus.RUNNING)
    validate_task_transition(TaskStatus.PENDING, TaskStatus.READY)
    validate_task_transition(TaskStatus.RUNNING, TaskStatus.SUCCEEDED)
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
