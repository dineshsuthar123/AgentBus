from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityName,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationContext,
    ToolResourceBudget,
    ToolSafetyClassification,
    ToolVersion,
)


def _capability() -> ToolCapability:
    return ToolCapability(
        name=ToolCapabilityName.FILESYSTEM_READ,
        scope=CapabilityScope(
            roots=("C:/repo",),
            patterns=("src/**", "tests/**"),
        ),
    )


def test_versioned_descriptor_and_invocation_are_immutable() -> None:
    capability = _capability()
    descriptor = ToolDescriptor(
        name="filesystem.read",
        version=ToolVersion(major=1),
        description="Read a bounded text file inside the assigned worktree.",
        capabilities=(capability,),
        argument_schema={"type": "object"},
        output_schema={"type": "object"},
        safety=ToolSafetyClassification.SAFE,
    )
    invocation = ToolInvocation(
        invocation_id="inv-1",
        run_id="run-1",
        task_id="step-1",
        tool_name=descriptor.name,
        tool_version=descriptor.version,
        arguments={"path": "src/app.py"},
        requested_capabilities=(capability,),
        context=ToolInvocationContext(
            workspace_identity="C:/repo",
            worktree_identity="C:/repo",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
    )

    assert invocation.protocol_version == "1.0"
    assert invocation.resource_budget.wall_clock_seconds == 90
    with pytest.raises(ValidationError):
        invocation.invocation_revision = 2


@pytest.mark.parametrize("name", ["all", "*", "**", "unrestricted"])
def test_unrestricted_capability_scope_is_rejected(name: str) -> None:
    with pytest.raises(ValidationError, match="unrestricted"):
        CapabilityScope(roots=(name,))


def test_descriptor_rejects_capability_free_and_unbounded_schema() -> None:
    with pytest.raises(ValidationError, match="at least one capability"):
        ToolDescriptor(
            name="repository.scan",
            version=ToolVersion(major=1),
            description="Scan repository metadata.",
            capabilities=(),
            argument_schema={"type": "object"},
            output_schema={"type": "object"},
        )

    with pytest.raises(ValidationError, match="65536 bytes"):
        ToolDescriptor(
            name="repository.scan",
            version=ToolVersion(major=1),
            description="Scan repository metadata.",
            capabilities=(_capability(),),
            argument_schema={"description": "x" * 70_000},
            output_schema={"type": "object"},
        )


def test_resource_budget_rejects_reset_and_inconsistent_output_limits() -> None:
    with pytest.raises(ValidationError, match="combined_output_bytes"):
        ToolResourceBudget(
            stdout_bytes=10,
            stderr_bytes=10,
            combined_output_bytes=21,
        )
    with pytest.raises(ValidationError, match="invocations_per_task"):
        ToolResourceBudget(invocations_per_task=10, invocations_per_run=9)


def test_scope_deduplicates_without_mutating_order() -> None:
    scope = CapabilityScope(patterns=("src/**", "tests/**", "src/**"))
    assert scope.patterns == ("src/**", "tests/**")
