from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic.json_schema import models_json_schema

from agentbus.control.app import ControlAppContext, create_app
from agentbus.control.models import (
    CONTROL_PROTOCOL_VERSION,
    ProtocolModel,
)


def build_openapi() -> dict[str, Any]:
    query = _SchemaQuery()
    app = create_app(
        token="schema-only-token-with-at-least-thirty-two-bytes",
        query_service=query,
        supervisor=_SchemaSupervisor(),
        context=ControlAppContext(
            daemon_id="schema",
            host="127.0.0.1",
            port=0,
            started_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            state_database="state.db",
        ),
        shutdown_supervisor=False,
    )
    schema = app.openapi()
    schema["info"]["version"] = CONTROL_PROTOCOL_VERSION
    schema["info"]["x-agentbus-protocol-version"] = CONTROL_PROTOCOL_VERSION
    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque-session-token",
        "description": "Token delivered only through the daemon startup handshake.",
    }
    schema["security"] = [{"BearerAuth": []}]
    schema["paths"]["/health"]["get"]["security"] = []
    return schema


def build_json_schema() -> dict[str, Any]:
    models = sorted(_protocol_models(), key=lambda model: model.__name__)
    _, schema = models_json_schema(
        [(model, "validation") for model in models],
        title="AgentBus Control Protocol v1",
    )
    schema["$id"] = "https://agentbus.invalid/protocol/agentbus-v1.schema.json"
    schema["x-agentbus-protocol-version"] = CONTROL_PROTOCOL_VERSION
    return schema


def generate_typescript(schema: dict[str, Any]) -> str:
    definitions = schema.get("$defs", {})
    lines = [
        "/* Generated from protocol/agentbus-v1.schema.json. Do not edit. */",
        "",
        f"export const CONTROL_PROTOCOL_VERSION = {json.dumps(CONTROL_PROTOCOL_VERSION)} as const;",
        "",
    ]
    for name in sorted(definitions):
        definition = definitions[name]
        if definition.get("type") == "object" or "properties" in definition:
            lines.extend(_typescript_interface(name, definition))
        else:
            lines.append(
                f"export type {name} = {_typescript_type(definition)};"
            )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_protocol(output_dir: str | Path, *, check: bool = False) -> int:
    output = Path(output_dir)
    root = output.parent if output.name == "protocol" else Path.cwd()
    artifacts = {
        output / "agentbus-v1.openapi.json": _json_text(build_openapi()),
        output / "agentbus-v1.schema.json": _json_text(build_json_schema()),
        output / "README.md": _protocol_readme(),
    }
    typescript_path = root / "extensions" / "vscode" / "src" / "generated" / "protocol.ts"
    artifacts[typescript_path] = generate_typescript(build_json_schema())
    changed = [
        path
        for path, content in artifacts.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    if check and changed:
        names = ", ".join(path.as_posix() for path in changed)
        raise RuntimeError(f"Generated control protocol artifacts are stale: {names}")
    if not check:
        for path, content in artifacts.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    return len(changed)


def _protocol_models() -> set[type[ProtocolModel]]:
    discovered: set[type[ProtocolModel]] = set()
    pending = list(ProtocolModel.__subclasses__())
    while pending:
        model = pending.pop()
        if model in discovered:
            continue
        discovered.add(model)
        pending.extend(model.__subclasses__())
    return discovered


def _typescript_interface(name: str, schema: dict[str, Any]) -> list[str]:
    required = set(schema.get("required", []))
    lines = [f"export interface {name} {{"]
    for property_name, property_schema in schema.get("properties", {}).items():
        optional = "" if property_name in required else "?"
        lines.append(
            f"  {json.dumps(property_name)}{optional}: "
            f"{_typescript_type(property_schema)};"
        )
    if schema.get("additionalProperties") not in (False, None):
        lines.append("  [key: string]: unknown;")
    lines.extend(["}", ""])
    return lines


def _typescript_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", maxsplit=1)[-1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"]) or "never"
    if "anyOf" in schema:
        return " | ".join(_typescript_type(item) for item in schema["anyOf"])
    if "oneOf" in schema:
        return " | ".join(_typescript_type(item) for item in schema["oneOf"])
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return " | ".join(
            _typescript_type({**schema, "type": item}) for item in schema_type
        )
    if schema_type == "array":
        return f"Array<{_typescript_type(schema.get('items', {}))}>"
    if schema_type == "object":
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return f"Record<string, {_typescript_type(additional)}>"
        properties = schema.get("properties")
        if properties:
            members = [
                f"{json.dumps(key)}: {_typescript_type(value)}"
                for key, value in properties.items()
            ]
            return "{ " + "; ".join(members) + " }"
        return "Record<string, unknown>"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "null":
        return "null"
    if schema_type == "string":
        return "string"
    return "unknown"


def _json_text(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _protocol_readme() -> str:
    return """# AgentBus Control Protocol v1

This directory is generated from the Python control-plane application and
Pydantic transport models.

- `agentbus-v1.openapi.json` describes the authenticated HTTP and SSE API.
- `agentbus-v1.schema.json` contains the shared transport model definitions.
- `../extensions/vscode/src/generated/protocol.ts` is generated from the JSON Schema.

Run `agentbus control-schema export` after changing protocol models. Run
`agentbus control-schema export --check` in CI to detect stale artifacts.

The health endpoint is unauthenticated. Every `/api/v1` endpoint uses an opaque
bearer token delivered once through the daemon parent-process handshake.
Generated files contain no concrete token, credential, or machine-specific path.

Cancellation lifecycle fields are optional protocol v1 extensions with safe
defaults. Run, scheduler, report, and cancel responses share the same model.
Persisted cancellation events are monotonic and replayable; payloads contain no
prompts, bearer tokens, API keys, environment dumps, or raw provider objects.

Managed tool protocol types are also additive control v1 extensions. Generated
schemas cover tool descriptors, exact capabilities, policy decisions, resource
budgets and usage, scoped approvals, invocation results, cancellation,
artifacts, immutable audit entries, MCP server diagnostics, and the defaulted
run-report `tool_runtime` summary. The embedded managed-tool protocol is
`agentbus.tool` version `1.0`.

The control API supports bounded tool registry and policy inspection,
diagnostic-only policy evaluation, paginated run invocation and audit reads,
run-scoped tool cancellation, and configured local MCP diagnostics. It does not
expose arbitrary command execution, raw environment values, subprocess handles,
or model-controlled MCP server configuration.

Authenticated `POST /mcp` is a constrained MCP JSON-RPC endpoint and therefore
is documented separately from the REST OpenAPI paths. It shares the same local
daemon authentication and response sanitization. See
`../docs/mcp-integration.md`.
"""


class _SchemaQuery:
    store = None
    workspace_service = None
    repository = None


class _SchemaSupervisor:
    def shutdown(self, *, wait: bool = True) -> None:
        return None
