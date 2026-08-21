from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st
from pydantic import ValidationError

from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools.protocol import (
    CapabilityScope,
    StructuredToolCall,
    ToolArtifact,
    ToolCapability,
    ToolCapabilityName,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationContext,
    ToolInvocationStatus,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolProtocolValidationError,
    ToolProtocolVersionError,
    ToolResourceBudget,
    ToolResult,
    ToolVersion,
    capability_fingerprint,
    sha256_json,
    validate_descriptor,
    validate_invocation_against_descriptor,
)
from agentbus.tools.registry import ToolVersionMismatchError
from agentbus.tools.runtime import build_managed_tool_runtime


FUZZ_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    derandomize=True,
    database=None,
)
SECRET = "AZURE_OPENAI_API_KEY=tool-protocol-private-value"
_CAPABILITY = ToolCapability(
    name=ToolCapabilityName.FILESYSTEM_READ,
    scope=CapabilityScope(roots=("C:/repo",), patterns=("src/**",)),
)
_BASE_DESCRIPTOR = {
    "name": "filesystem.read",
    "version": {"major": 1},
    "description": "Read one bounded file.",
    "capabilities": [_CAPABILITY.model_dump(mode="json")],
    "argument_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 2_048}
        },
    },
    "output_schema": {"type": "object"},
}


def _descriptor(**updates) -> ToolDescriptor:
    return ToolDescriptor.model_validate(_BASE_DESCRIPTOR | updates)


def _invocation(
    *,
    arguments: dict | None = None,
    capabilities: tuple[ToolCapability, ...] = (_CAPABILITY,),
    version: ToolVersion | None = None,
) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="inv-fuzz",
        run_id="run-fuzz",
        task_id="task-fuzz",
        tool_name="filesystem.read",
        tool_version=version or ToolVersion(major=1),
        arguments=arguments if arguments is not None else {"path": "src/app.py"},
        requested_capabilities=capabilities,
        context=ToolInvocationContext(
            workspace_identity="C:/repo",
            worktree_identity="C:/repo",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=False,
        ),
    )


def _policy_decision(invocation: ToolInvocation) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        outcome=ToolPolicyOutcome.ALLOW,
        rule_id="allow.fuzz",
        reason="Protocol fuzz fixture.",
        invocation_id=invocation.invocation_id,
        invocation_revision=invocation.invocation_revision,
        capability_fingerprint=capability_fingerprint(
            invocation.requested_capabilities
        ),
        arguments_sha256=sha256_json(invocation.arguments),
    )


_SCOPE_FIELDS = (
    "roots",
    "patterns",
    "affected_paths",
    "executables",
    "working_directories",
    "network_destinations",
    "environment_keys",
    "git_operations",
    "mcp_servers",
)


@st.composite
def _malformed_scopes(draw: st.DrawFn) -> dict:
    case = draw(
        st.sampled_from(
            (
                "unrestricted",
                "empty",
                "nul",
                "long",
                "many",
                "network",
                "wrong_type",
                "extra",
            )
        )
    )
    field = draw(st.sampled_from(_SCOPE_FIELDS))
    if case == "unrestricted":
        return {field: [draw(st.sampled_from(("*", "**", "all", "unrestricted")))]}
    if case == "empty":
        return {field: [draw(st.sampled_from(("", " ", "\t", "\n")))]}
    if case == "nul":
        return {field: ["safe" + chr(0) + "unsafe"]}
    if case == "long":
        return {field: ["x" * draw(st.integers(min_value=1_025, max_value=2_048))]}
    if case == "many":
        return {field: [f"value-{index}" for index in range(257)]}
    if case == "network":
        return {
            "network_allowed": False,
            "network_destinations": ["example.invalid"],
        }
    if case == "wrong_type":
        return {
            field: draw(
                st.none()
                | st.integers()
                | st.dictionaries(
                    st.text(max_size=8),
                    st.integers(),
                    max_size=4,
                )
            )
        }
    return {"unknown_scope": ["outside"]}


@FUZZ_SETTINGS
@given(payload=_malformed_scopes())
def test_malformed_capability_scopes_fail_closed(payload: dict) -> None:
    with pytest.raises(ValidationError):
        CapabilityScope.model_validate(payload)


@st.composite
def _invalid_capability_arrays(draw: st.DrawFn):
    case = draw(
        st.sampled_from(
            ("empty", "duplicate", "many", "unknown", "malformed_scope", "scalar")
        )
    )
    serialized = _CAPABILITY.model_dump(mode="json")
    if case == "empty":
        return []
    if case == "duplicate":
        return [serialized, serialized]
    if case == "many":
        return [
            {
                "name": ToolCapabilityName.FILESYSTEM_READ.value,
                "scope": {"roots": [f"C:/repo/{index}"]},
            }
            for index in range(65)
        ]
    if case == "unknown":
        suffix = draw(
            st.text(
                alphabet="abcdefghijklmnopqrstuvwxyz",
                min_size=1,
                max_size=64,
            )
        )
        return [{"name": f"unknown.{suffix}", "scope": {}}]
    if case == "malformed_scope":
        return [
            {
                "name": ToolCapabilityName.FILESYSTEM_READ.value,
                "scope": draw(_malformed_scopes()),
            }
        ]
    return draw(st.none() | st.integers() | st.text(max_size=64))


@FUZZ_SETTINGS
@given(capabilities=_invalid_capability_arrays())
def test_invalid_descriptor_capability_arrays_are_rejected(capabilities) -> None:
    with pytest.raises(ValidationError):
        ToolDescriptor.model_validate(
            _BASE_DESCRIPTOR | {"capabilities": capabilities}
        )


def test_duplicate_capabilities_are_rejected_by_every_request_model() -> None:
    duplicate = (_CAPABILITY, _CAPABILITY)

    with pytest.raises(ValidationError, match="unique"):
        _descriptor(capabilities=duplicate)
    with pytest.raises(ValidationError, match="unique"):
        StructuredToolCall(
            tool_name="filesystem.read",
            arguments={"path": "src/app.py"},
            expected_capabilities=duplicate,
        )
    with pytest.raises(ValidationError, match="unique"):
        _invocation(capabilities=duplicate)


_INVALID_SCHEMAS = st.sampled_from(
    (
        {"type": "not-a-json-type"},
        {"required": "path"},
        {"minLength": -1},
        {"type": "array", "items": "invalid"},
        {"properties": []},
        {"additionalProperties": "no"},
    )
)


@FUZZ_SETTINGS
@given(
    field=st.sampled_from(("argument_schema", "output_schema")),
    schema=_INVALID_SCHEMAS,
)
def test_invalid_json_schemas_fail_closed(field: str, schema: dict) -> None:
    descriptor = _descriptor(**{field: schema})

    with pytest.raises(
        ToolProtocolValidationError,
        match="not a valid JSON Schema",
    ) as captured:
        validate_invocation_against_descriptor(_invocation(), descriptor)

    assert SECRET not in str(captured.value)


@st.composite
def _invalid_resource_budgets(draw: st.DrawFn) -> dict:
    case = draw(
        st.sampled_from(
            (
                "wall_clock",
                "stdout",
                "stderr",
                "combined",
                "artifact",
                "children",
                "concurrency",
                "task_count",
                "run_count",
                "mutation_count",
                "written",
                "file",
                "memory",
                "cpu",
                "inconsistent_output",
                "inconsistent_counts",
            )
        )
    )
    invalid_ranges = {
        "wall_clock": (
            "wall_clock_seconds",
            st.one_of(
                st.floats(max_value=0, allow_nan=False, allow_infinity=False),
                st.floats(min_value=86_401, allow_nan=False, allow_infinity=False),
                st.sampled_from((math.inf, math.nan)),
            ),
        ),
        "stdout": (
            "stdout_bytes",
            st.one_of(st.integers(max_value=-1), st.integers(min_value=16_777_217)),
        ),
        "stderr": (
            "stderr_bytes",
            st.one_of(st.integers(max_value=-1), st.integers(min_value=16_777_217)),
        ),
        "combined": (
            "combined_output_bytes",
            st.one_of(st.integers(max_value=-1), st.integers(min_value=33_554_433)),
        ),
        "artifact": (
            "artifact_bytes",
            st.one_of(st.integers(max_value=-1), st.integers(min_value=268_435_457)),
        ),
        "children": (
            "child_processes",
            st.one_of(st.integers(max_value=-1), st.integers(min_value=257)),
        ),
        "concurrency": (
            "concurrent_processes",
            st.one_of(st.integers(max_value=0), st.integers(min_value=65)),
        ),
        "task_count": (
            "invocations_per_task",
            st.one_of(st.integers(max_value=0), st.integers(min_value=10_001)),
        ),
        "run_count": (
            "invocations_per_run",
            st.one_of(st.integers(max_value=0), st.integers(min_value=100_001)),
        ),
        "mutation_count": (
            "file_mutations",
            st.one_of(st.integers(max_value=-1), st.integers(min_value=100_001)),
        ),
        "written": (
            "total_written_bytes",
            st.one_of(st.integers(max_value=-1), st.integers(min_value=1_073_741_825)),
        ),
        "file": (
            "maximum_file_bytes",
            st.one_of(st.integers(max_value=-1), st.integers(min_value=268_435_457)),
        ),
        "memory": ("memory_bytes", st.integers(max_value=1_048_575)),
        "cpu": (
            "cpu_seconds",
            st.one_of(
                st.floats(max_value=0, allow_nan=False, allow_infinity=False),
                st.floats(min_value=86_401, allow_nan=False, allow_infinity=False),
                st.sampled_from((math.inf, math.nan)),
            ),
        ),
    }
    if case == "inconsistent_output":
        return {
            "stdout_bytes": draw(st.integers(min_value=0, max_value=100)),
            "stderr_bytes": 0,
            "combined_output_bytes": 101,
        }
    if case == "inconsistent_counts":
        run_limit = draw(st.integers(min_value=1, max_value=9_999))
        return {
            "invocations_per_task": run_limit + 1,
            "invocations_per_run": run_limit,
        }
    field, values = invalid_ranges[case]
    return {field: draw(values)}


@FUZZ_SETTINGS
@given(payload=_invalid_resource_budgets())
def test_invalid_resource_budgets_are_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ToolResourceBudget.model_validate(payload)


@st.composite
def _invalid_json_payloads(draw: st.DrawFn) -> dict:
    case = draw(
        st.sampled_from(("oversized", "bytes", "set", "nan", "infinity"))
    )
    if case == "oversized":
        size = draw(st.integers(min_value=1_048_577, max_value=1_052_672))
        return {"value": "x" * size}
    if case == "bytes":
        return {"value": b"not-json"}
    if case == "set":
        return {"value": {"not", "json"}}
    if case == "nan":
        return {"value": math.nan}
    return {"value": math.inf}


@FUZZ_SETTINGS
@given(payload=_invalid_json_payloads())
def test_excessive_or_non_json_arguments_are_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        StructuredToolCall(
            tool_name="filesystem.read",
            arguments=payload,
            expected_capabilities=(_CAPABILITY,),
        )
    with pytest.raises(ValidationError):
        _invocation(arguments=payload)


@FUZZ_SETTINGS
@given(payload=_invalid_json_payloads())
def test_giant_or_non_json_structured_outputs_are_rejected(payload: dict) -> None:
    invocation = _invocation()

    with pytest.raises(ValidationError):
        ToolResult(
            invocation_id=invocation.invocation_id,
            invocation_revision=invocation.invocation_revision,
            status=ToolInvocationStatus.SUCCEEDED,
            structured_output=payload,
            policy_decision=_policy_decision(invocation),
        )


_BASE_ARTIFACT = {
    "artifact_id": "artifact-fuzz",
    "kind": "report",
    "relative_path": "reports/result.json",
    "media_type": "application/json",
    "size_bytes": 2,
    "sha256": "0" * 64,
    "safe_metadata": {"source": "fuzz"},
}


@st.composite
def _malformed_artifacts(draw: st.DrawFn) -> dict:
    case = draw(
        st.sampled_from(
            (
                "identifier",
                "kind",
                "path",
                "media",
                "size",
                "digest",
                "metadata_size",
                "metadata_type",
                "extra",
            )
        )
    )
    if case == "identifier":
        return _BASE_ARTIFACT | {"artifact_id": "a" * 129}
    if case == "kind":
        suffix = draw(
            st.text(
                alphabet="abcdefghijklmnopqrstuvwxyz",
                min_size=1,
                max_size=32,
            )
        )
        return _BASE_ARTIFACT | {"kind": f"unknown-{suffix}"}
    if case == "path":
        return _BASE_ARTIFACT | {
            "relative_path": "x" * draw(
                st.integers(min_value=2_049, max_value=2_200)
            )
        }
    if case == "media":
        return _BASE_ARTIFACT | {
            "media_type": "x" * draw(st.integers(min_value=256, max_value=512))
        }
    if case == "size":
        return _BASE_ARTIFACT | {"size_bytes": draw(st.integers(max_value=-1))}
    if case == "digest":
        return _BASE_ARTIFACT | {
            "sha256": draw(
                st.sampled_from(("", "0" * 63, "0" * 65, "g" * 64))
            )
        }
    if case == "metadata_size":
        return _BASE_ARTIFACT | {
            "safe_metadata": {
                "value": "x"
                * draw(st.integers(min_value=65_537, max_value=66_000))
            }
        }
    if case == "metadata_type":
        return _BASE_ARTIFACT | {"safe_metadata": {"value": b"not-json"}}
    return _BASE_ARTIFACT | {"unexpected": "field"}


@FUZZ_SETTINGS
@given(payload=_malformed_artifacts())
def test_malformed_artifact_metadata_is_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ToolArtifact.model_validate(payload)


@FUZZ_SETTINGS
@given(
    version=st.one_of(
        st.integers(min_value=0, max_value=65_535)
        .filter(lambda value: value != 1)
        .map(lambda value: f"{value}.0"),
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789.-",
            min_size=1,
            max_size=64,
        ).map(lambda value: f"unsupported-{value}"),
    )
)
def test_unknown_protocol_versions_fail_closed(version: str) -> None:
    descriptor = _descriptor(protocol_version=version)

    with pytest.raises(ToolProtocolVersionError, match="Unsupported"):
        validate_descriptor(descriptor)


def test_invalid_process_arguments_cannot_spawn_or_persist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, store = _managed_runtime(tmp_path)
    descriptor = runtime.registry.descriptor("process.execute")
    call = StructuredToolCall(
        tool_name=descriptor.name,
        arguments={
            "executable": "python",
            "arguments": "must-be-an-array",
        },
        expected_capabilities=descriptor.capabilities,
    )
    spawned: list[object] = []

    def reject_spawn(*args, **kwargs):
        spawned.append((args, kwargs))
        raise AssertionError("process creation occurred before validation")

    monkeypatch.setattr(
        "agentbus.sandbox.process.subprocess.Popen",
        reject_spawn,
    )
    try:
        with pytest.raises(
            ToolProtocolValidationError,
            match="JSON Schema validation",
        ):
            runtime.invoke(
                call,
                run_id="run-fuzz",
                task_id="task-fuzz",
                caller_role="coder",
                workspace_trusted=True,
                provider_consented=False,
            )
        assert spawned == []
        assert store.list_tool_invocations("run-fuzz") == []
    finally:
        runtime.close()


def test_unknown_tool_version_cannot_spawn_or_persist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, store = _managed_runtime(tmp_path)
    descriptor = runtime.registry.descriptor("process.execute")
    call = StructuredToolCall(
        tool_name=descriptor.name,
        arguments={
            "executable": "python",
            "arguments": ["-c", "raise SystemExit(99)"],
        },
        expected_capabilities=descriptor.capabilities,
    )
    invocation = runtime.invocation_from_call(
        call,
        run_id="run-fuzz",
        task_id="task-fuzz",
        caller_role="coder",
        workspace_trusted=True,
        provider_consented=False,
        invocation_id="inv-unknown-version",
    )
    unknown_version = ToolVersion(
        major=descriptor.version.major + 1,
        minor=descriptor.version.minor,
        patch=descriptor.version.patch,
    )
    invocation = invocation.model_copy(
        update={"tool_version": unknown_version}
    )
    spawned: list[object] = []

    def reject_spawn(*args, **kwargs):
        spawned.append((args, kwargs))
        raise AssertionError("unknown tool version reached process creation")

    monkeypatch.setattr(
        "agentbus.sandbox.process.subprocess.Popen",
        reject_spawn,
    )
    try:
        with pytest.raises(ToolVersionMismatchError):
            runtime.dispatch(invocation)
        assert spawned == []
        assert store.list_tool_invocations("run-fuzz") == []
    finally:
        runtime.close()


def _managed_runtime(root: Path):
    store = StateStore(root / "state.db")
    store.create_run_with_tasks(
        RunRecord(
            run_id="run-fuzz",
            original_task="Fuzz tool validation",
            model="fake",
            workspace=str(root.resolve()),
            planner_output={"goal": "test", "steps": []},
            graph_data={"version": 1, "tasks": []},
        ),
        [
            TaskSpec(
                task_id="task-fuzz",
                title="Reject malformed tool input",
                description="Prove validation precedes process creation.",
            )
        ],
    )
    runtime = build_managed_tool_runtime(
        workspace=root,
        state_store=store,
        executable_catalog=ExecutableCatalog({"python": sys.executable}),
    )
    return runtime, store
