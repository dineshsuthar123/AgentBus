from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import jsonschema
import pytest

from agentbus.tools import (
    DuplicateToolError,
    ToolExecutionOutput,
    ToolNotFoundError,
    ToolRegistry,
    ToolVersionMismatchError,
    builtin_descriptors,
)
from agentbus.tools.protocol import ToolDescriptor, ToolInvocation, ToolVersion


@dataclass
class _FakeTool:
    descriptor: ToolDescriptor

    def execute(self, invocation: ToolInvocation, *, cancellation=None):
        return ToolExecutionOutput(structured_output={"ok": True})


def test_builtin_descriptors_are_deterministic_and_workspace_scoped(
    tmp_path: Path,
) -> None:
    descriptors = builtin_descriptors(workspace=tmp_path)
    by_name = {descriptor.name: descriptor for descriptor in descriptors}

    assert len(descriptors) == 18
    assert len(by_name) == len(descriptors)
    assert by_name["filesystem.read"].capabilities[0].scope.roots == (
        str(tmp_path.resolve()),
    )
    assert by_name["process.execute"].capabilities[0].scope.network_allowed is False
    assert by_name["git.commit"].idempotent is False
    assert by_name["filesystem.rename"].capabilities[0].name.value == (
        "filesystem.rename"
    )
    assert by_name["git.stage"].capabilities[0].scope.git_operations == ("stage",)
    assert by_name["git.show"].capabilities[0].scope.git_operations == ("show",)
    for descriptor in descriptors:
        jsonschema.validators.validator_for(descriptor.argument_schema).check_schema(
            descriptor.argument_schema
        )
        jsonschema.validators.validator_for(descriptor.output_schema).check_schema(
            descriptor.output_schema
        )


def test_builtin_argument_schemas_reject_unknown_and_unbounded_fields(
    tmp_path: Path,
) -> None:
    by_name = {
        descriptor.name: descriptor
        for descriptor in builtin_descriptors(workspace=tmp_path)
    }

    jsonschema.validate(
        {"path": "module.py", "maximum_bytes": 1024},
        by_name["filesystem.read"].argument_schema,
    )
    jsonschema.validate(
        {"paths": ["module.py"], "message": "feat: module"},
        by_name["git.commit"].argument_schema,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"path": "module.py", "unknown": True},
            by_name["filesystem.read"].argument_schema,
        )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"paths": [f"file-{index}.txt" for index in range(257)]},
            by_name["git.stage"].argument_schema,
        )


def test_registry_rejects_duplicates_without_partial_batch_registration(
    tmp_path: Path,
) -> None:
    descriptors = builtin_descriptors(workspace=tmp_path)
    registry = ToolRegistry()
    registry.register(_FakeTool(descriptors[0]))

    with pytest.raises(DuplicateToolError, match="already registered"):
        registry.register(_FakeTool(descriptors[0]))
    with pytest.raises(DuplicateToolError, match="batch"):
        registry.register_many(
            (
                (descriptors[1], lambda: _FakeTool(descriptors[1])),
                (descriptors[1], lambda: _FakeTool(descriptors[1])),
            )
        )
    assert descriptors[1].name not in registry


def test_registry_lazily_initializes_once_and_lists_in_sorted_order(
    tmp_path: Path,
) -> None:
    descriptors = builtin_descriptors(workspace=tmp_path)[:3]
    created: list[str] = []
    registry = ToolRegistry()
    for descriptor in reversed(descriptors):
        registry.register_factory(
            descriptor,
            lambda descriptor=descriptor: _record_tool(descriptor, created),
        )

    assert created == []
    assert [item.name for item in registry.descriptors()] == sorted(
        item.name for item in descriptors
    )
    first = registry.resolve(descriptors[0].name)
    second = registry.resolve(descriptors[0].name)
    assert first is second
    assert created == [descriptors[0].name]


def test_registry_rejects_unknown_and_mismatched_versions(tmp_path: Path) -> None:
    descriptor = builtin_descriptors(workspace=tmp_path)[0]
    registry = ToolRegistry()
    registry.register(_FakeTool(descriptor))

    with pytest.raises(ToolNotFoundError, match="Unknown tool"):
        registry.resolve("missing.tool")
    with pytest.raises(ToolVersionMismatchError, match="not 2.0.0"):
        registry.resolve(descriptor.name, version=ToolVersion(major=2))


def test_lazy_factory_descriptor_must_match_registration(tmp_path: Path) -> None:
    descriptors = builtin_descriptors(workspace=tmp_path)
    registry = ToolRegistry()
    registry.register_factory(
        descriptors[0],
        lambda: _FakeTool(descriptors[1]),
    )

    with pytest.raises(ValueError, match="descriptor differs"):
        registry.resolve(descriptors[0].name)


def _record_tool(descriptor: ToolDescriptor, created: list[str]) -> _FakeTool:
    created.append(descriptor.name)
    return _FakeTool(descriptor)
