from __future__ import annotations

from typing import Any

import jsonschema

from agentbus.tools.protocol.errors import (
    ToolCapabilityEscalationError,
    ToolProtocolValidationError,
    ToolProtocolVersionError,
)
from agentbus.tools.protocol.models import (
    CapabilityScope,
    ToolCapability,
    ToolDescriptor,
    ToolInvocation,
)
from agentbus.tools.protocol.serialization import capability_fingerprint
from agentbus.tools.protocol.version import is_supported_protocol_version


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


def validate_protocol_version(version: str) -> None:
    if not is_supported_protocol_version(version):
        raise ToolProtocolVersionError(
            f"Unsupported tool protocol version: {version}."
        )


def validate_descriptor(descriptor: ToolDescriptor) -> None:
    validate_protocol_version(descriptor.protocol_version)
    _validate_schema(descriptor.argument_schema, "Tool argument schema")
    _validate_schema(descriptor.output_schema, "Tool output schema")


def validate_invocation_against_descriptor(
    invocation: ToolInvocation,
    descriptor: ToolDescriptor,
) -> None:
    validate_protocol_version(invocation.protocol_version)
    validate_descriptor(descriptor)
    if invocation.protocol_version != descriptor.protocol_version:
        raise ToolProtocolVersionError(
            "Invocation and descriptor protocol versions do not match."
        )
    if invocation.tool_name != descriptor.name:
        raise ToolProtocolValidationError(
            "Invocation tool name does not match the resolved descriptor."
        )
    if invocation.tool_version != descriptor.version:
        raise ToolProtocolValidationError(
            "Invocation tool version does not match the resolved descriptor."
        )
    if invocation.timeout_seconds > descriptor.maximum_timeout_seconds:
        raise ToolProtocolValidationError(
            "Invocation timeout exceeds the tool descriptor maximum."
        )
    validate_tool_arguments(invocation.arguments, descriptor)

    declared_names = {capability.name for capability in descriptor.capabilities}
    requested_names = {
        capability.name for capability in invocation.requested_capabilities
    }
    undeclared = requested_names - declared_names
    if undeclared:
        names = ", ".join(sorted(item.value for item in undeclared))
        raise ToolCapabilityEscalationError(
            f"Invocation requested undeclared capabilities: {names}."
        )
    if not capability_set_contains(
        descriptor.capabilities,
        invocation.requested_capabilities,
    ):
        raise ToolCapabilityEscalationError(
            "Invocation capability scope exceeds the descriptor declaration."
        )


def validate_tool_arguments(
    arguments: dict[str, Any],
    descriptor: ToolDescriptor,
) -> None:
    _validate_instance(
        arguments,
        descriptor.argument_schema,
        "Tool arguments",
    )


def validate_tool_output(
    output: dict[str, Any],
    descriptor: ToolDescriptor,
) -> None:
    _validate_instance(
        output,
        descriptor.output_schema,
        "Structured tool output",
    )


def scope_contains(allowed: CapabilityScope, requested: CapabilityScope) -> bool:
    if requested.network_allowed and not allowed.network_allowed:
        return False
    for field_name in _SCOPE_FIELDS:
        allowed_values = set(getattr(allowed, field_name))
        requested_values = set(getattr(requested, field_name))
        if (
            field_name == "affected_paths"
            and not allowed_values
            and allowed.roots
        ):
            # Concrete paths narrow a root-scoped descriptor. Policy and the
            # contained implementation still validate each path against roots.
            continue
        if not requested_values.issubset(allowed_values):
            return False
    return True


def capability_set_contains(
    allowed: tuple[ToolCapability, ...],
    requested: tuple[ToolCapability, ...],
) -> bool:
    for requested_capability in requested:
        matching = (
            allowed_capability
            for allowed_capability in allowed
            if allowed_capability.name == requested_capability.name
        )
        if not any(
            scope_contains(allowed_capability.scope, requested_capability.scope)
            for allowed_capability in matching
        ):
            return False
    return True


def require_capabilities_unchanged(
    approved: tuple[ToolCapability, ...],
    current: tuple[ToolCapability, ...],
) -> None:
    approved_fingerprint = capability_fingerprint(approved)
    current_fingerprint = capability_fingerprint(current)
    if approved_fingerprint != current_fingerprint:
        raise ToolCapabilityEscalationError(
            "Invocation capabilities changed after authorization."
        )


def require_invocation_revision(
    *,
    approved_revision: int,
    current_revision: int,
) -> None:
    if approved_revision != current_revision:
        raise ToolCapabilityEscalationError(
            "Invocation revision changed after authorization."
        )


def _validate_schema(schema: dict[str, Any], label: str) -> None:
    try:
        validator = jsonschema.validators.validator_for(schema)
        validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ToolProtocolValidationError(
            f"{label} is not a valid JSON Schema."
        ) from exc


def _validate_instance(
    value: dict[str, Any],
    schema: dict[str, Any],
    label: str,
) -> None:
    _validate_schema(schema, label)
    validator = jsonschema.validators.validator_for(schema)(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    error = next(validator.iter_errors(value), None)
    if error is not None:
        # The underlying jsonschema message may contain argument values.
        raise ToolProtocolValidationError(
            f"{label} failed descriptor JSON Schema validation."
        ) from error
