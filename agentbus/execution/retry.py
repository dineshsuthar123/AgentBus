from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from agentbus.execution.models import FailureCategory, RetryPolicy


class TaskExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: FailureCategory = FailureCategory.UNKNOWN,
        retryable: bool | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


@dataclass(frozen=True)
class FailureClassification:
    category: FailureCategory
    retryable: bool | None
    message: str


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    exhausted: bool
    delay_seconds: float


class FailureClassifier:
    """Maps executor failures into stable categories without inspecting secrets."""

    def classify(self, error: Exception) -> FailureClassification:
        if isinstance(error, TaskExecutionError):
            return FailureClassification(error.category, error.retryable, str(error))

        if isinstance(error, (json.JSONDecodeError, ValidationError)):
            return FailureClassification(
                FailureCategory.MODEL_OUTPUT_ERROR,
                None,
                str(error),
            )

        if isinstance(error, (ConnectionError, TimeoutError)):
            return FailureClassification(
                FailureCategory.MODEL_TRANSPORT_ERROR,
                None,
                str(error),
            )

        if isinstance(error, (PermissionError, ValueError)):
            return FailureClassification(
                FailureCategory.POLICY_VIOLATION,
                False,
                str(error),
            )

        return FailureClassification(FailureCategory.UNKNOWN, None, str(error))


class RetryController:
    def __init__(self, policy: RetryPolicy | None = None):
        self.policy = policy or RetryPolicy()

    def decide(
        self,
        *,
        category: FailureCategory,
        attempt_number: int,
        task_maximum_attempts: int,
        retryable_override: bool | None = None,
    ) -> RetryDecision:
        maximum_attempts = min(
            self.policy.maximum_attempts,
            task_maximum_attempts,
        )
        retryable = (
            retryable_override
            if retryable_override is not None
            else category in self.policy.retryable_categories
        )
        exhausted = attempt_number >= maximum_attempts
        should_retry = retryable and not exhausted
        delay = self.delay_for(attempt_number + 1) if should_retry else 0.0
        return RetryDecision(should_retry, exhausted, delay)

    def delay_for(self, next_attempt_number: int) -> float:
        exponent = max(0, next_attempt_number - 2)
        delay = self.policy.initial_delay_seconds * (
            self.policy.delay_multiplier**exponent
        )
        return min(delay, self.policy.maximum_delay_seconds)
