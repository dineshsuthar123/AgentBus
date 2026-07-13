from __future__ import annotations

import math
import time
from threading import Lock

from agentbus.evaluation.errors import EvaluationBudgetExceeded
from agentbus.models.types import ModelResult


class EvaluationBudget:
    """Enforces request, estimated-token, and wall-clock limits before calls."""

    def __init__(
        self,
        *,
        max_requests: int,
        max_tokens: int,
        timeout_seconds: float,
        clock=time.monotonic,
    ):
        if (
            max_requests < 1
            or max_tokens < 1
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("evaluation budgets must be positive")
        self.max_requests = max_requests
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.started_at = clock()
        self.requests = 0
        self.reserved_tokens = 0
        self.actual_tokens = 0
        self._lock = Lock()

    def reserve(self, prompt: str, *, maximum_output_tokens: int = 512) -> None:
        estimated_input = max(1, math.ceil(len(prompt) / 4))
        estimated_total = estimated_input + maximum_output_tokens
        with self._lock:
            self._check_time()
            if self.requests + 1 > self.max_requests:
                raise EvaluationBudgetExceeded(
                    f"Evaluation request budget exhausted ({self.max_requests})."
                )
            if self.reserved_tokens + estimated_total > self.max_tokens:
                raise EvaluationBudgetExceeded(
                    "Evaluation token budget would be exceeded before provider call."
                )
            self.requests += 1
            self.reserved_tokens += estimated_total

    def record(self, result: ModelResult) -> None:
        with self._lock:
            self._check_time()
            self.actual_tokens += result.usage.total_tokens or 0
            if self.actual_tokens > self.max_tokens:
                raise EvaluationBudgetExceeded(
                    "Provider-reported usage exceeded the evaluation token budget."
                )

    def check_time(self) -> None:
        with self._lock:
            self._check_time()

    def remaining_seconds(self) -> float:
        return max(0.0, self.timeout_seconds - (self.clock() - self.started_at))

    def snapshot(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "max_requests": self.max_requests,
                "max_tokens": self.max_tokens,
                "timeout_seconds": self.timeout_seconds,
                "requests": self.requests,
                "reserved_tokens": self.reserved_tokens,
                "actual_tokens": self.actual_tokens,
                "elapsed_seconds": max(0.0, self.clock() - self.started_at),
            }

    def _check_time(self) -> None:
        if self.clock() - self.started_at > self.timeout_seconds:
            raise EvaluationBudgetExceeded(
                f"Evaluation wall-clock budget exhausted ({self.timeout_seconds}s)."
            )
