from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict

from agentbus.tools.protocol import ToolCapabilityName


_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "contains",
        "default",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxContains",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minContains",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "pattern",
        "patternProperties",
        "propertyNames",
        "uniqueItems",
        "unevaluatedProperties",
    }
)
_MAX_SCHEMA_NODES = 10_000
_MAX_ISSUES = 128
_MAX_ARGUMENTS_JSON_BYTES = 1_048_576


@dataclass(frozen=True)
class AzureSchemaIssue:
    path: str
    code: str
    message: str


class AzureStructuredOutputSchemaError(ValueError):
    def __init__(self, issues: tuple[AzureSchemaIssue, ...]):
        self.issues = issues
        details = "; ".join(
            f"{issue.path}: {issue.message}" for issue in issues[:8]
        )
        if len(issues) > 8:
            details += f"; and {len(issues) - 8} more issue(s)"
        super().__init__(f"Azure structured-output schema is incompatible: {details}")


class AzureWireValueError(ValueError):
    """Raised when an Azure wire value cannot become an authoritative model."""


class _AzureWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AzureModelToolCallWire(_AzureWireModel):
    """Azure-safe transport for a locally validated model tool call."""

    tool_name: str
    arguments_json: str
    expected_capabilities: list[ToolCapabilityName]
    timeout_seconds: float | None
    invocation_revision: int
    idempotency_key: str


class AzureAgentActionWire(_AzureWireModel):
    action: Literal["tool_call", "finish"]
    tool_call: AzureModelToolCallWire | None
    summary: str | None


class AzurePlanStepWire(_AzureWireModel):
    id: str
    title: str
    description: str
    risk: Literal["low", "medium", "high"]
    dependencies: list[str] | None
    assigned_role: str
    maximum_attempts: int
    expected_outputs: list[str]
    done_criteria: list[str] | None
    required_capabilities: list[ToolCapabilityName] | None
    targeted_files: list[str] | None
    targeted_symbols: list[str] | None
    expected_impacted_components: list[str] | None
    proposed_tests: list[str] | None
    architecture_constraints: list[str] | None


class AzurePlannerOutputWire(_AzureWireModel):
    goal: str
    steps: list[AzurePlanStepWire]
    test_strategy: str
    done_criteria: list[str]
    targeted_files: list[str] | None
    targeted_symbols: list[str] | None
    expected_impacted_components: list[str] | None
    proposed_tests: list[str] | None
    architecture_constraints: list[str] | None
    intelligence_snapshot_id: str | None
    intelligence_context_hash: str | None
    intelligence_warnings: list[str] | None
    intelligence_scope_validated: bool | None


class AzureReviewIssueWire(_AzureWireModel):
    severity: Literal["low", "medium", "high"]
    message: str
    file: str | None


class AzureReviewerOutputWire(_AzureWireModel):
    approved: bool
    issues: list[AzureReviewIssueWire]
    summary: str
    required_fixes: list[str]
    unplanned_affected_components: list[str]
    missing_tests: list[str]
    boundary_violations: list[str]
    index_uncertainty: list[str]


@dataclass(frozen=True)
class AzureStructuredOutputAdapter:
    authoritative_model: type[BaseModel]
    wire_model: type[BaseModel]
    converter: Callable[[BaseModel], dict[str, Any]]
    explicit_wire: bool = False

    def decode_value(self, value: Any) -> BaseModel:
        wire_value = self.wire_model.model_validate(value)
        payload = self.converter(wire_value)
        return self.authoritative_model.model_validate(payload)

    def decode_json(self, value: str) -> BaseModel:
        wire_value = self.wire_model.model_validate_json(value)
        payload = self.converter(wire_value)
        return self.authoritative_model.model_validate(payload)


def azure_structured_output_adapter(
    model: type[BaseModel],
) -> AzureStructuredOutputAdapter:
    """Select an explicit Azure wire contract without changing local models."""
    from agentbus.agents.planner import PlannerOutput
    from agentbus.agents.reviewer import ReviewerOutput
    from agentbus.runtime.schemas import AgentAction

    if model is AgentAction:
        adapter = AzureStructuredOutputAdapter(
            authoritative_model=model,
            wire_model=AzureAgentActionWire,
            converter=_convert_agent_action,
            explicit_wire=True,
        )
    elif model is PlannerOutput:
        adapter = AzureStructuredOutputAdapter(
            authoritative_model=model,
            wire_model=AzurePlannerOutputWire,
            converter=_model_payload,
            explicit_wire=True,
        )
    elif model is ReviewerOutput:
        adapter = AzureStructuredOutputAdapter(
            authoritative_model=model,
            wire_model=AzureReviewerOutputWire,
            converter=_model_payload,
            explicit_wire=True,
        )
    else:
        return AzureStructuredOutputAdapter(
            authoritative_model=model,
            wire_model=model,
            converter=_model_payload,
        )

    validate_azure_structured_output_schema(adapter.wire_model.model_json_schema())
    return adapter


def validate_azure_structured_output_schema(schema: dict[str, Any]) -> None:
    """Fail locally when a schema is outside Azure's strict supported subset."""
    if not isinstance(schema, dict):
        raise TypeError("Azure structured-output schema must be a JSON object.")

    issues: list[AzureSchemaIssue] = []
    seen: set[int] = set()
    node_count = 0

    def add(path: str, code: str, message: str) -> None:
        if len(issues) < _MAX_ISSUES:
            issues.append(AzureSchemaIssue(path, code, message))

    def visit(value: Any, path: str) -> None:
        nonlocal node_count
        if len(issues) >= _MAX_ISSUES:
            return
        if isinstance(value, dict):
            identity = id(value)
            if identity in seen:
                add(path, "cyclic_schema", "schema contains a cyclic Python object")
                return
            seen.add(identity)
            node_count += 1
            if node_count > _MAX_SCHEMA_NODES:
                add(path, "schema_too_large", "schema exceeds the bounded node limit")
                seen.remove(identity)
                return

            for keyword in sorted(_UNSUPPORTED_KEYWORDS.intersection(value)):
                add(
                    f"{path}.{keyword}",
                    "unsupported_keyword",
                    f"keyword '{keyword}' is not supported",
                )

            properties = value.get("properties")
            is_object = value.get("type") == "object" or isinstance(properties, dict)
            if is_object:
                if value.get("additionalProperties") is not False:
                    add(
                        f"{path}.additionalProperties",
                        "open_object",
                        "object schemas must set additionalProperties to false",
                    )
                if isinstance(properties, dict):
                    required = value.get("required")
                    property_names = set(properties)
                    required_names = (
                        set(required)
                        if isinstance(required, list)
                        and all(isinstance(item, str) for item in required)
                        else set()
                    )
                    missing = sorted(property_names - required_names)
                    extra = sorted(required_names - property_names)
                    if missing:
                        add(
                            f"{path}.required",
                            "missing_required_fields",
                            "all object fields must be required; missing "
                            + ", ".join(missing),
                        )
                    if extra:
                        add(
                            f"{path}.required",
                            "unknown_required_fields",
                            "required contains unknown fields: " + ", ".join(extra),
                        )

            for key, child in value.items():
                visit(child, f"{path}.{key}")
            seen.remove(identity)
            return

        if isinstance(value, list):
            node_count += 1
            if node_count > _MAX_SCHEMA_NODES:
                add(path, "schema_too_large", "schema exceeds the bounded node limit")
                return
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(schema, "$")
    if issues:
        raise AzureStructuredOutputSchemaError(tuple(issues))


def azure_schema_diagnostic_metadata(
    error: AzureStructuredOutputSchemaError,
) -> dict[str, Any]:
    return {
        "schema_issue_count": len(error.issues),
        "schema_issue_codes": sorted({issue.code for issue in error.issues})[:16],
        "schema_issue_paths": [issue.path[:256] for issue in error.issues[:16]],
    }


def _model_payload(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="python")


def _convert_agent_action(value: BaseModel) -> dict[str, Any]:
    if not isinstance(value, AzureAgentActionWire):
        raise TypeError("Azure action converter received an unexpected wire model.")
    payload = value.model_dump(mode="python")
    if value.tool_call is None:
        return payload

    arguments_text = value.tool_call.arguments_json
    try:
        encoded = arguments_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AzureWireValueError("Tool arguments JSON must be valid UTF-8.") from exc
    if len(encoded) > _MAX_ARGUMENTS_JSON_BYTES:
        raise AzureWireValueError(
            "Tool arguments JSON must be at most 1048576 bytes."
        )
    try:
        arguments = json.loads(
            arguments_text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, AzureWireValueError) as exc:
        raise AzureWireValueError("Tool arguments JSON is malformed.") from exc
    if not isinstance(arguments, dict):
        raise AzureWireValueError("Tool arguments JSON must decode to an object.")

    payload["tool_call"] = {
        "tool_name": value.tool_call.tool_name,
        "arguments": arguments,
        "expected_capabilities": value.tool_call.expected_capabilities,
        "timeout_seconds": value.tool_call.timeout_seconds,
        "invocation_revision": value.tool_call.invocation_revision,
        "idempotency_key": value.tool_call.idempotency_key,
    }
    return payload


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AzureWireValueError("Tool arguments JSON contains duplicate keys.")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise AzureWireValueError(f"Unsupported JSON constant: {value}")
