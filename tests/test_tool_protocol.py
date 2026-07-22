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
    ToolInvocationStatus,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolResourceBudget,
    ToolResult,
    ToolSafetyClassification,
    ToolVersion,
    bound_text,
    capability_fingerprint,
    deserialize_protocol_model,
    require_capabilities_unchanged,
    require_invocation_revision,
    safe_protocol_dict,
    serialize_protocol_model,
    sha256_json,
    validate_invocation_against_descriptor,
    validate_protocol_version,
)
from agentbus.tools.protocol.errors import (
    ToolCapabilityEscalationError,
    ToolProtocolValidationError,
    ToolProtocolVersionError,
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


def test_protocol_serialization_is_stable_and_redacts_sensitive_arguments() -> None:
    invocation = _invocation(arguments={"path": "src/app.py", "token": "secret"})
    serialized = serialize_protocol_model(invocation)

    assert "secret" not in serialized
    assert "[REDACTED]" in serialized
    assert serialize_protocol_model(invocation) == serialized
    assert deserialize_protocol_model(
        serialize_protocol_model(invocation, safe=False),
        ToolInvocation,
    ) == invocation
    assert safe_protocol_dict(invocation)["arguments"]["token"] == "[REDACTED]"


def test_capability_fingerprint_is_order_independent() -> None:
    read = _capability()
    execute = ToolCapability(
        name=ToolCapabilityName.PROCESS_EXECUTE,
        scope=CapabilityScope(executables=("python",)),
    )
    assert capability_fingerprint((read, execute)) == capability_fingerprint(
        (execute, read)
    )


def test_descriptor_validation_rejects_downgrade_and_capability_expansion() -> None:
    descriptor = _descriptor()
    with pytest.raises(ToolProtocolVersionError, match="Unsupported"):
        validate_protocol_version("0.9")

    invocation = _invocation(
        capabilities=(
            _capability(),
            ToolCapability(name=ToolCapabilityName.PROCESS_NETWORK),
        )
    )
    with pytest.raises(ToolCapabilityEscalationError, match="undeclared"):
        validate_invocation_against_descriptor(invocation, descriptor)

    with pytest.raises(ToolProtocolValidationError, match="timeout"):
        validate_invocation_against_descriptor(
            _invocation(timeout_seconds=91),
            descriptor,
        )


def test_approval_binding_rejects_scope_and_revision_changes() -> None:
    approved = (_capability(),)
    expanded = (
        ToolCapability(
            name=ToolCapabilityName.FILESYSTEM_READ,
            scope=CapabilityScope(
                roots=("C:/repo", "C:/other"),
                patterns=("src/**", "tests/**"),
            ),
        ),
    )
    with pytest.raises(ToolCapabilityEscalationError, match="changed"):
        require_capabilities_unchanged(approved, expanded)
    with pytest.raises(ToolCapabilityEscalationError, match="revision"):
        require_invocation_revision(approved_revision=1, current_revision=2)


def test_bounded_text_preserves_utf8_and_reports_truncation() -> None:
    text, byte_count, truncated = bound_text("alpha-\u20ac-omega", 9)
    assert text == "alpha-\u20ac"
    assert byte_count == 9
    assert truncated is True


def test_structured_protocol_payloads_are_byte_bounded() -> None:
    with pytest.raises(ValidationError, match="tool arguments must be at most"):
        _invocation(arguments={"content": "x" * 1_048_577})

    invocation = _invocation()
    decision = ToolPolicyDecision(
        outcome=ToolPolicyOutcome.ALLOW,
        rule_id="allow.read_only",
        reason="Bounded read",
        invocation_id=invocation.invocation_id,
        invocation_revision=invocation.invocation_revision,
        capability_fingerprint=capability_fingerprint(
            invocation.requested_capabilities
        ),
        arguments_sha256=sha256_json(invocation.arguments),
    )
    with pytest.raises(ValidationError, match="structured tool output must be at most"):
        ToolResult(
            invocation_id=invocation.invocation_id,
            invocation_revision=invocation.invocation_revision,
            status=ToolInvocationStatus.SUCCEEDED,
            structured_output={"content": "x" * 1_048_577},
            policy_decision=decision,
        )
    with pytest.raises(ValidationError, match="tool result metadata must be at most"):
        ToolResult(
            invocation_id=invocation.invocation_id,
            invocation_revision=invocation.invocation_revision,
            status=ToolInvocationStatus.SUCCEEDED,
            policy_decision=decision,
            safe_diagnostic_metadata={"summary": "x" * 65_537},
        )


def _descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="filesystem.read",
        version=ToolVersion(major=1),
        description="Read a bounded text file inside the assigned worktree.",
        capabilities=(_capability(),),
        argument_schema={"type": "object"},
        output_schema={"type": "object"},
        safety=ToolSafetyClassification.SAFE,
    )


def _invocation(
    *,
    arguments: dict | None = None,
    capabilities: tuple[ToolCapability, ...] | None = None,
    timeout_seconds: float = 90,
) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="inv-1",
        run_id="run-1",
        task_id="step-1",
        tool_name="filesystem.read",
        tool_version=ToolVersion(major=1),
        arguments=arguments or {"path": "src/app.py"},
        requested_capabilities=capabilities or (_capability(),),
        context=ToolInvocationContext(
            workspace_identity="C:/repo",
            worktree_identity="C:/repo",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
        timeout_seconds=timeout_seconds,
    )
