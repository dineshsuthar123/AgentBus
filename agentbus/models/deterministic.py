from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any

from pydantic import BaseModel, ValidationError

from agentbus.execution.cancellation import (
    CancellationRequested,
    CancellationState,
    CancellationToken,
)
from agentbus.models.base import validate_json_schema
from agentbus.models.errors import (
    ModelOutputError,
    ModelCancellationError,
    ModelSchemaValidationError,
    ModelServiceUnavailableError,
    ModelTimeoutError,
)
from agentbus.models.types import ModelResult, ModelRole, ModelUsage


_DELETE_PROFILE_CONTENT = "deterministic deletion target\n"
_DELETE_PROFILE_SHA256 = hashlib.sha256(
    _DELETE_PROFILE_CONTENT.encode("utf-8")
).hexdigest()
_RUNTIME_STEP_PATTERN = re.compile(r"--- Step (\d+) ---")
_PROFILE_REQUIREMENTS: dict[str, list[str]] = {
    "tool-safe-read": ["filesystem.read"],
    "tool-atomic-write": ["filesystem.write", "filesystem.create"],
    "tool-source-patch": ["filesystem.write"],
    "tool-pytest": ["test.execute", "process.execute"],
    "tool-git-diff": ["git.read"],
    "tool-git-commit": [
        "filesystem.write",
        "filesystem.create",
        "git.write",
        "git.commit",
    ],
    "tool-delete-approval": ["filesystem.delete"],
    "tool-deny-outside-read": ["filesystem.read"],
    "tool-deny-credential-read": ["filesystem.read"],
    "tool-process-timeout": ["process.execute"],
    "tool-process-cancel": ["process.execute"],
    "tool-excessive-output": ["process.execute"],
    "tool-budget-exhaustion": ["filesystem.read"],
    "tool-local-mcp": ["mcp.connect", "mcp.invoke"],
    "tool-loop-limit": ["filesystem.read"],
    "tool-control-acceptance": [
        "filesystem.read",
        "filesystem.write",
        "filesystem.create",
        "test.execute",
        "process.execute",
    ],
}
_PROFILE_OUTPUTS: dict[str, list[str]] = {
    "tool-atomic-write": ["profile_result.txt"],
    "tool-source-patch": ["module.py"],
    "tool-git-commit": ["profile_commit.py"],
    "tool-control-acceptance": ["acceptance_tool.py"],
}


class DeterministicProvider:
    """Offline provider that exercises the production model interface."""

    def __init__(
        self,
        *,
        role: ModelRole | str,
        model: str = "deterministic-v1",
        profile: str = "python-calculator",
        latency_seconds: float = 0.0,
        failure_kind: str = "service_unavailable",
        failure_calls: tuple[int, ...] = (),
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if latency_seconds < 0:
            raise ValueError("deterministic latency must be non-negative")
        if any(call < 1 for call in failure_calls):
            raise ValueError("deterministic failure calls must be positive")
        self.role = ModelRole(role)
        self._model = model
        self.profile = profile
        self.latency_seconds = float(latency_seconds)
        self.failure_kind = failure_kind
        self.failure_calls = frozenset(failure_calls)
        self.sleeper = sleeper
        self._lock = threading.Lock()
        self._total_calls = 0
        self._scope_calls: dict[str, int] = {}

    @property
    def provider_name(self) -> str:
        return "deterministic"

    @property
    def model_name(self) -> str:
        return self._model

    def generate_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ModelResult:
        call_number, scope_call, scope = self._next_call(metadata)
        return self._generate(
            prompt=prompt,
            schema=None,
            metadata=metadata or {},
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
            call_number=call_number,
            scope_call=scope_call,
            scope=scope,
            json_requested=False,
        )

    def generate_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | dict[str, Any] | None = None,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ModelResult:
        call_number, scope_call, scope = self._next_call(metadata)
        return self._generate(
            prompt=prompt,
            schema=schema,
            metadata=metadata or {},
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
            call_number=call_number,
            scope_call=scope_call,
            scope=scope,
            json_requested=True,
        )

    def _generate(
        self,
        *,
        prompt: str,
        schema: type[BaseModel] | dict[str, Any] | None,
        metadata: dict[str, Any],
        timeout_seconds: float | None,
        cancellation: CancellationToken | None,
        call_number: int,
        scope_call: int,
        scope: str,
        json_requested: bool,
    ) -> ModelResult:
        source = f"provider:{self.provider_name}"
        task_id = str(metadata.get("task_id") or "") or None
        operation = (
            cancellation.operation(
                f"{self.provider_name}.{self.role.value}.generate",
                source=source,
                interruptible=True,
                provider=self.provider_name,
                task_id=task_id,
            )
            if cancellation is not None
            else nullcontext()
        )
        try:
            with operation:
                self._before_output(
                    call_number,
                    timeout_seconds,
                    cancellation=cancellation,
                )
                if json_requested:
                    value = self._json_value(
                        scope_call,
                        metadata,
                        schema,
                        prompt=prompt,
                    )
                    value = self._validate(value, schema, call_number)
                else:
                    value = self._text_value(scope_call)
                if cancellation is not None:
                    cancellation.checkpoint(
                        source,
                        stage="after-output",
                        provider=self.provider_name,
                    )
                return self._result(
                    value,
                    prompt=prompt,
                    call_number=call_number,
                    scope_call=scope_call,
                    scope=scope,
                    cancellation=(
                        cancellation.snapshot() if cancellation is not None else None
                    ),
                )
        except CancellationRequested as exc:
            raise ModelCancellationError(
                "Deterministic provider acknowledged cancellation.",
                provider=self.provider_name,
                model=self.model_name,
                metadata={
                    "acknowledgement_source": exc.source,
                    "acknowledgement_stage": exc.stage,
                    "cancellation_supported": True,
                },
            ) from exc

    def _next_call(
        self,
        metadata: dict[str, Any] | None,
    ) -> tuple[int, int, str]:
        safe_metadata = metadata or {}
        run_id = str(safe_metadata.get("run_id") or "global")
        task_id = str(safe_metadata.get("task_id") or "run")
        scope = f"{run_id}:{task_id}"
        with self._lock:
            self._total_calls += 1
            self._scope_calls[scope] = self._scope_calls.get(scope, 0) + 1
            return self._total_calls, self._scope_calls[scope], scope

    def _before_output(
        self,
        call_number: int,
        timeout_seconds: float | None,
        *,
        cancellation: CancellationToken | None,
    ) -> None:
        if self.latency_seconds:
            if timeout_seconds is not None and self.latency_seconds > timeout_seconds:
                raise ModelTimeoutError(
                    "Deterministic provider exceeded its configured timeout.",
                    provider=self.provider_name,
                    model=self.model_name,
                )
            if cancellation is not None:
                if cancellation.wait(self.latency_seconds):
                    cancellation.checkpoint(
                        f"provider:{self.provider_name}",
                        stage="latency",
                        provider=self.provider_name,
                    )
            else:
                self.sleeper(self.latency_seconds)
        if call_number not in self.failure_calls:
            return
        arguments = {
            "provider": self.provider_name,
            "model": self.model_name,
            "metadata": {"deterministic_call": call_number},
        }
        if self.failure_kind == "output_error":
            raise ModelOutputError("Injected deterministic output failure.", **arguments)
        if self.failure_kind == "timeout":
            raise ModelTimeoutError("Injected deterministic timeout.", **arguments)
        raise ModelServiceUnavailableError(
            "Injected deterministic service failure.",
            **arguments,
        )

    def _json_value(
        self,
        scope_call: int,
        metadata: dict[str, Any],
        schema: type[BaseModel] | dict[str, Any] | None,
        *,
        prompt: str,
    ) -> dict[str, Any]:
        if (
            isinstance(schema, type)
            and issubclass(schema, BaseModel)
            and set(schema.model_fields) == {"status"}
        ):
            return {"status": "ok"}
        if self.role == ModelRole.PLANNER:
            return self._plan()
        if self.role == ModelRole.REVIEWER:
            return {
                "approved": True,
                "issues": [],
                "summary": "Deterministic review approved verified local changes.",
                "required_fixes": [],
            }
        if self.role == ModelRole.SUMMARIZER:
            return {"summary": self._text_value(scope_call)}
        return self._coder_action(
            scope_call,
            str(metadata.get("task_id") or "step-1"),
            prompt=prompt,
        )

    def _plan(self) -> dict[str, Any]:
        if self.profile in _PROFILE_REQUIREMENTS:
            outputs = _PROFILE_OUTPUTS.get(self.profile, [])
            return {
                "goal": f"Exercise deterministic profile {self.profile}.",
                "steps": [
                    {
                        "id": "step-1",
                        "title": f"Execute {self.profile}",
                        "description": (
                            "Exercise one production managed-tool scenario through "
                            "the deterministic provider."
                        ),
                        "risk": "low",
                        "dependencies": [],
                        "assigned_role": "coder",
                        "maximum_attempts": 2,
                        "expected_outputs": outputs,
                        "done_criteria": [
                            "The managed-tool outcome is persisted and auditable."
                        ],
                        "required_capabilities": _PROFILE_REQUIREMENTS[self.profile],
                    }
                ],
                "test_strategy": "Inspect the persisted deterministic tool result.",
                "done_criteria": [
                    "Policy, dispatch, cancellation, and audit paths remain active."
                ],
            }
        steps = [
            {
                "id": "step-1",
                "title": "Implement deterministic calculator",
                "description": (
                    "Create a small calculator module and its deterministic test."
                ),
                "risk": "low",
                "dependencies": [],
                "assigned_role": "coder",
                "maximum_attempts": 2,
                "expected_outputs": [
                    "agentbus_result.py",
                    "test_agentbus_result.py",
                ],
                "done_criteria": [
                    "The calculator adds two integers.",
                    "The deterministic test passes.",
                ],
                "required_capabilities": [
                    "filesystem.write",
                    "filesystem.create",
                    "test.execute",
                    "process.execute",
                    "git.read",
                ],
            }
        ]
        if self.profile == "cancellation-two-task":
            steps.append(
                {
                    "id": "step-2",
                    "title": "Create downstream deterministic artifact",
                    "description": (
                        "Create a second artifact only if scheduling remains active."
                    ),
                    "risk": "low",
                    "dependencies": [],
                    "assigned_role": "coder",
                    "maximum_attempts": 1,
                    "expected_outputs": ["agentbus_secondary.py"],
                    "done_criteria": ["The secondary artifact is present."],
                    "required_capabilities": [
                        "filesystem.write",
                        "filesystem.create",
                    ],
                }
            )
        return {
            "goal": "Complete a deterministic offline AgentBus execution.",
            "steps": steps,
            "test_strategy": "Run the repository's detected pytest command.",
            "done_criteria": [
                "All planned files are created.",
                "Verification and review pass.",
            ],
        }

    def _coder_action(
        self,
        scope_call: int,
        task_id: str,
        *,
        prompt: str,
    ) -> dict[str, Any]:
        action_position = _runtime_action_position(prompt, scope_call)
        if self.profile in _PROFILE_REQUIREMENTS:
            if self.profile == "tool-loop-limit":
                return _tool_action(
                    "repository.scan",
                    {},
                    ["filesystem.read"],
                    f"{task_id}:bounded-loop-{action_position}",
                )
            actions = self._profile_actions(task_id)
            return actions[min(action_position, len(actions)) - 1]
        if task_id == "step-2":
            actions = [
                {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "filesystem.write",
                        "arguments": {
                            "path": "agentbus_secondary.py",
                            "content": 'MESSAGE = "scheduled"\n',
                        },
                        "expected_capabilities": [
                            "filesystem.write",
                            "filesystem.create",
                        ],
                        "idempotency_key": f"{task_id}:write-secondary",
                    },
                },
                {
                    "action": "finish",
                    "summary": "Created the secondary deterministic artifact.",
                },
            ]
        else:
            actions = [
                {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "filesystem.write",
                        "arguments": {
                            "path": "agentbus_result.py",
                            "content": (
                                '"""Deterministic AgentBus acceptance artifact."""\n\n'
                                "def add(left: int, right: int) -> int:\n"
                                "    return left + right\n"
                            ),
                        },
                        "expected_capabilities": [
                            "filesystem.write",
                            "filesystem.create",
                        ],
                        "idempotency_key": f"{task_id}:write-result",
                    },
                },
                {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "filesystem.write",
                        "arguments": {
                            "path": "test_agentbus_result.py",
                            "content": (
                                "from agentbus_result import add\n\n\n"
                                "def test_add() -> None:\n"
                                "    assert add(2, 3) == 5\n"
                            ),
                        },
                        "expected_capabilities": [
                            "filesystem.write",
                            "filesystem.create",
                        ],
                        "idempotency_key": f"{task_id}:write-test",
                    },
                },
                {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "test.execute",
                        "arguments": {
                            "executable": "python",
                            "arguments": ["-m", "pytest", "-q"],
                        },
                        "expected_capabilities": [
                            "test.execute",
                            "process.execute",
                        ],
                        "idempotency_key": f"{task_id}:pytest",
                    },
                },
                {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "git.diff",
                        "arguments": {},
                        "expected_capabilities": ["git.read"],
                        "idempotency_key": f"{task_id}:git-diff",
                    },
                },
                {
                    "action": "finish",
                    "summary": (
                        "Created the deterministic calculator and passing test."
                    ),
                },
            ]
        return actions[min(action_position, len(actions)) - 1]

    def _profile_actions(self, task_id: str) -> list[dict[str, Any]]:
        finish = {
            "action": "finish",
            "summary": f"Completed deterministic profile {self.profile}.",
        }
        calls: dict[str, list[dict[str, Any]]] = {
            "tool-safe-read": [
                _tool_action(
                    "filesystem.read",
                    {"path": "README.md"},
                    ["filesystem.read"],
                    f"{task_id}:safe-read",
                )
            ],
            "tool-atomic-write": [
                _tool_action(
                    "filesystem.write",
                    {
                        "path": "profile_result.txt",
                        "content": "deterministic atomic write\n",
                    },
                    ["filesystem.write", "filesystem.create"],
                    f"{task_id}:atomic-write",
                )
            ],
            "tool-source-patch": [
                _tool_action(
                    "filesystem.patch",
                    {
                        "path": "module.py",
                        "expected": "VALUE = 1",
                        "replacement": "VALUE = 2",
                        "expected_occurrences": 1,
                    },
                    ["filesystem.write"],
                    f"{task_id}:source-patch",
                )
            ],
            "tool-pytest": [
                _tool_action(
                    "test.execute",
                    {
                        "executable": "python",
                        "arguments": ["-m", "pytest", "-q"],
                    },
                    ["test.execute", "process.execute"],
                    f"{task_id}:pytest",
                )
            ],
            "tool-git-diff": [
                _tool_action(
                    "git.diff",
                    {},
                    ["git.read"],
                    f"{task_id}:git-diff",
                )
            ],
            "tool-git-commit": [
                _tool_action(
                    "filesystem.write",
                    {
                        "path": "profile_commit.py",
                        "content": "PROFILE_COMMIT = True\n",
                    },
                    ["filesystem.write", "filesystem.create"],
                    f"{task_id}:commit-write",
                ),
                _tool_action(
                    "git.commit",
                    {
                        "paths": ["profile_commit.py"],
                        "message": "test: deterministic managed commit",
                    },
                    ["git.write", "git.commit"],
                    f"{task_id}:git-commit",
                ),
            ],
            "tool-delete-approval": [
                _tool_action(
                    "filesystem.delete",
                    {
                        "path": "delete_me.txt",
                        "expected_sha256": _DELETE_PROFILE_SHA256,
                    },
                    ["filesystem.delete"],
                    f"{task_id}:delete",
                )
            ],
            "tool-deny-outside-read": [
                _tool_action(
                    "filesystem.read",
                    {"path": "../outside.txt"},
                    ["filesystem.read"],
                    f"{task_id}:outside-read",
                )
            ],
            "tool-deny-credential-read": [
                _tool_action(
                    "filesystem.read",
                    {"path": ".env"},
                    ["filesystem.read"],
                    f"{task_id}:credential-read",
                )
            ],
            "tool-process-timeout": [
                _tool_action(
                    "process.execute",
                    {
                        "executable": "python",
                        "arguments": ["-c", "import time; time.sleep(2)"],
                    },
                    ["process.execute"],
                    f"{task_id}:process-timeout",
                    timeout_seconds=0.05,
                )
            ],
            "tool-process-cancel": [
                _tool_action(
                    "process.execute",
                    {
                        "executable": "python",
                        "arguments": ["-c", "import time; time.sleep(30)"],
                    },
                    ["process.execute"],
                    f"{task_id}:process-cancel",
                    timeout_seconds=60.0,
                )
            ],
            "tool-excessive-output": [
                _tool_action(
                    "process.execute",
                    {
                        "executable": "python",
                        "arguments": [
                            "-c",
                            "import sys; sys.stdout.write('x' * 200000)",
                        ],
                    },
                    ["process.execute"],
                    f"{task_id}:excessive-output",
                )
            ],
            "tool-budget-exhaustion": [
                _tool_action(
                    "filesystem.read",
                    {"path": "README.md"},
                    ["filesystem.read"],
                    f"{task_id}:budget-read-1",
                ),
                _tool_action(
                    "filesystem.read",
                    {"path": "README.md"},
                    ["filesystem.read"],
                    f"{task_id}:budget-read-2",
                ),
            ],
            "tool-local-mcp": [
                _tool_action(
                    "mcp.fixture.echo",
                    {"message": "deterministic MCP hello"},
                    ["mcp.connect", "mcp.invoke"],
                    f"{task_id}:mcp-echo",
                )
            ],
            "tool-control-acceptance": [
                _tool_action(
                    "filesystem.read",
                    {"path": "README.md"},
                    ["filesystem.read"],
                    f"{task_id}:acceptance-read",
                ),
                _tool_action(
                    "filesystem.write",
                    {
                        "path": "acceptance_tool.py",
                        "content": (
                            "def add(left: int, right: int) -> int:\n"
                            "    return left + right\n"
                        ),
                    },
                    ["filesystem.write", "filesystem.create"],
                    f"{task_id}:acceptance-write",
                ),
                _tool_action(
                    "test.execute",
                    {
                        "executable": "python",
                        "arguments": [
                            "-m",
                            "pytest",
                            "-q",
                            "test_acceptance_tool.py",
                        ],
                    },
                    ["test.execute", "process.execute"],
                    f"{task_id}:acceptance-pytest",
                ),
            ],
        }
        return [*calls[self.profile], finish]

    def _text_value(self, scope_call: int) -> str:
        return (
            f"Deterministic {self.role.value} summary "
            f"for scoped call {scope_call}."
        )

    def _validate(
        self,
        value: dict[str, Any],
        schema: type[BaseModel] | dict[str, Any] | None,
        call_number: int,
    ) -> dict[str, Any]:
        request_id = self._request_id(call_number)
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            try:
                return schema.model_validate(value).model_dump(mode="json")
            except ValidationError as exc:
                raise ModelSchemaValidationError(
                    "Deterministic output failed local schema validation.",
                    provider=self.provider_name,
                    model=self.model_name,
                    request_id=request_id,
                ) from exc
        if isinstance(schema, dict):
            validate_json_schema(
                value,
                schema,
                provider=self.provider_name,
                model=self.model_name,
                request_id=request_id,
            )
        return value

    def _result(
        self,
        value: str | dict[str, Any],
        *,
        prompt: str,
        call_number: int,
        scope_call: int,
        scope: str,
        cancellation: CancellationState | None,
    ) -> ModelResult:
        output = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        input_tokens = max(1, (len(prompt) + 3) // 4)
        output_tokens = max(1, (len(output) + 3) // 4)
        return ModelResult(
            value=value,
            provider=self.provider_name,
            model=self.model_name,
            role=self.role,
            request_id=self._request_id(call_number),
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cached_tokens=0,
            ),
            finish_status="completed",
            latency_seconds=self.latency_seconds,
            cancellation_requested=bool(
                cancellation and cancellation.requested
            ),
            cancellation_acknowledged=bool(
                cancellation and cancellation.acknowledged
            ),
            cancellation_supported=True,
            completed_after_cancellation=False,
            provider_metadata={
                "runtime": "offline",
                "profile": self.profile,
                "deterministic_call": call_number,
                "scope_call": scope_call,
                "scope": scope,
            },
        )

    def _request_id(self, call_number: int) -> str:
        return f"det-{self.role.value}-{call_number:04d}"


def _runtime_action_position(prompt: str, fallback: int) -> int:
    if "Previous observations:" not in prompt or "Return the next JSON action." not in prompt:
        return fallback
    matches = _RUNTIME_STEP_PATTERN.findall(prompt)
    return (int(matches[-1]) + 1) if matches else 1


def _tool_action(
    tool_name: str,
    arguments: dict[str, Any],
    capabilities: list[str],
    idempotency_key: str,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    tool_call: dict[str, Any] = {
        "tool_name": tool_name,
        "arguments": arguments,
        "expected_capabilities": capabilities,
        "idempotency_key": idempotency_key,
    }
    if timeout_seconds is not None:
        tool_call["timeout_seconds"] = timeout_seconds
    return {"action": "tool_call", "tool_call": tool_call}
