from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
