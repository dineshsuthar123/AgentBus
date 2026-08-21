from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


_MAX_RULES = 128
_MAX_OCCURRENCE = 1_000_000
_MAX_SCOPE_CHARS = 128
_TEST_FACTORY_TOKEN = object()


class FailureInjectionPoint(str, Enum):
    PROVIDER_FAILURE = "provider_failure"
    PARSER_FAILURE = "parser_failure"
    SQLITE_BUSY = "sqlite_busy"
    FILESYSTEM_WRITE_FAILURE = "filesystem_write_failure"
    GIT_COMMAND_FAILURE = "git_command_failure"
    SUBPROCESS_TIMEOUT = "subprocess_timeout"
    MCP_FAILURE = "mcp_failure"
    DAEMON_TERMINATION = "daemon_termination"
    CANCELLATION = "cancellation"
    INDEX_FAILURE = "index_failure"
    TRACE_WRITE_FAILURE = "trace_write_failure"


class FailureProbe(Protocol):
    def __call__(
        self,
        point: FailureInjectionPoint,
        *,
        scope: str | None = None,
    ) -> bool: ...


@dataclass(frozen=True)
class FailureRule:
    point: FailureInjectionPoint
    occurrence: int = 1
    scope: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "point", FailureInjectionPoint(self.point))
        if not isinstance(self.occurrence, int) or isinstance(self.occurrence, bool):
            raise TypeError("failure occurrence must be an integer")
        if self.occurrence < 1 or self.occurrence > _MAX_OCCURRENCE:
            raise ValueError(
                f"failure occurrence must be between 1 and {_MAX_OCCURRENCE}"
            )
        object.__setattr__(self, "scope", _normalized_scope(self.scope))


@dataclass(frozen=True)
class FiredFailure:
    point: FailureInjectionPoint
    occurrence: int
    scope: str | None


class DeterministicFailureInjector:
    """Bounded, instance-local failure schedule for tests and local validation."""

    def __init__(
        self,
        rules: tuple[FailureRule, ...],
        *,
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not _TEST_FACTORY_TOKEN:
            raise TypeError(
                "Use DeterministicFailureInjector.for_testing() to opt in explicitly."
            )
        if len(rules) > _MAX_RULES:
            raise ValueError(f"failure schedules support at most {_MAX_RULES} rules")
        if any(not isinstance(rule, FailureRule) for rule in rules):
            raise TypeError("failure schedules require FailureRule instances")
        normalized = tuple(rules)
        identities = {
            (rule.point, rule.occurrence, rule.scope) for rule in normalized
        }
        if len(identities) != len(normalized):
            raise ValueError("failure schedules must not contain duplicate rules")
        self._rules = normalized
        self._scoped_rule_keys = frozenset(
            (rule.point, rule.scope)
            for rule in normalized
            if rule.scope is not None
        )
        self._point_calls: Counter[FailureInjectionPoint] = Counter()
        self._scoped_calls: Counter[tuple[FailureInjectionPoint, str]] = Counter()
        self._fired: list[FiredFailure] = []
        self._lock = threading.Lock()

    @classmethod
    def for_testing(cls, *rules: FailureRule) -> "DeterministicFailureInjector":
        return cls(tuple(rules), _factory_token=_TEST_FACTORY_TOKEN)

    def __call__(
        self,
        point: FailureInjectionPoint,
        *,
        scope: str | None = None,
    ) -> bool:
        normalized_point = FailureInjectionPoint(point)
        normalized_scope = _normalized_scope(scope)
        with self._lock:
            self._point_calls[normalized_point] += 1
            point_occurrence = self._point_calls[normalized_point]
            scoped_occurrence = point_occurrence
            if normalized_scope is not None:
                scoped_key = (normalized_point, normalized_scope)
                if scoped_key in self._scoped_rule_keys:
                    self._scoped_calls[scoped_key] += 1
                    scoped_occurrence = self._scoped_calls[scoped_key]
            for rule in self._rules:
                if rule.point != normalized_point:
                    continue
                if rule.scope is not None and rule.scope != normalized_scope:
                    continue
                occurrence = (
                    scoped_occurrence if rule.scope is not None else point_occurrence
                )
                if rule.occurrence != occurrence:
                    continue
                self._fired.append(
                    FiredFailure(
                        point=normalized_point,
                        occurrence=occurrence,
                        scope=rule.scope,
                    )
                )
                return True
        return False

    @property
    def fired(self) -> tuple[FiredFailure, ...]:
        with self._lock:
            return tuple(self._fired)

    @property
    def all_rules_fired(self) -> bool:
        with self._lock:
            fired = {
                (item.point, item.occurrence, item.scope) for item in self._fired
            }
            expected = {
                (rule.point, rule.occurrence, rule.scope) for rule in self._rules
            }
        return fired == expected

    def calls(
        self,
        point: FailureInjectionPoint,
        *,
        scope: str | None = None,
    ) -> int:
        normalized_point = FailureInjectionPoint(point)
        normalized_scope = _normalized_scope(scope)
        with self._lock:
            if normalized_scope is None:
                return self._point_calls[normalized_point]
            return self._scoped_calls[(normalized_point, normalized_scope)]


def failure_due(
    probe: FailureProbe | None,
    point: FailureInjectionPoint,
    *,
    scope: str | None = None,
) -> bool:
    if probe is None:
        return False
    result = probe(point, scope=scope)
    if not isinstance(result, bool):
        raise TypeError("failure probes must return a boolean")
    return result


def _normalized_scope(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("failure scope must be a string")
    if not value or len(value) > _MAX_SCOPE_CHARS:
        raise ValueError(
            f"failure scope must contain between 1 and {_MAX_SCOPE_CHARS} characters"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("failure scope must not contain control characters")
    return value


__all__ = [
    "DeterministicFailureInjector",
    "FailureInjectionPoint",
    "FailureProbe",
    "FailureRule",
    "FiredFailure",
    "failure_due",
]
