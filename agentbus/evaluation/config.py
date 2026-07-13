from __future__ import annotations

import os
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationConfig:
    results_dir: str = ".agentbus/evaluations"
    fixture_root: str | None = None
    preserve_fixtures: bool = False
    max_requests: int = 100
    max_tokens: int = 10_000
    timeout_seconds: float = 300.0

    @classmethod
    def from_env(cls) -> "EvaluationConfig":
        return cls(
            results_dir=os.getenv("AGENTBUS_EVAL_RESULTS_DIR", cls.results_dir),
            fixture_root=_text("AGENTBUS_EVAL_FIXTURE_ROOT"),
            preserve_fixtures=_boolean(
                "AGENTBUS_EVAL_PRESERVE_FIXTURES", cls.preserve_fixtures
            ),
            max_requests=_integer(
                "AGENTBUS_EVAL_MAX_REQUESTS", cls.max_requests, minimum=1
            ),
            max_tokens=_integer(
                "AGENTBUS_EVAL_MAX_TOKENS", cls.max_tokens, minimum=1
            ),
            timeout_seconds=_number(
                "AGENTBUS_EVAL_TIMEOUT_SECONDS", cls.timeout_seconds, minimum=0.001
            ),
        )


def _text(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _boolean(name: str, default: bool) -> bool:
    value = _text(name)
    if value is None:
        return default
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _integer(name: str, default: int, *, minimum: int) -> int:
    value = _text(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return parsed


def _number(name: str, default: float, *, minimum: float) -> float:
    value = _text(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(parsed) or parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return parsed
