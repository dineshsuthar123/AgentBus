from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentbus.control.protocol import (
    build_json_schema,
    build_openapi,
    export_protocol,
    generate_typescript,
)


def test_openapi_declares_bearer_security_except_health() -> None:
    schema = build_openapi()

    assert schema["security"] == [{"BearerAuth": []}]
    assert schema["paths"]["/health"]["get"]["security"] == []
    assert (
        schema["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
    )
    assert schema["info"]["x-agentbus-protocol-version"] == "1.0"


def test_json_schema_generates_typescript_from_shared_models() -> None:
    schema = build_json_schema()
    generated = generate_typescript(schema)

    assert "export interface RunCreateRequest" in generated
    assert "export interface ErrorResponse" in generated
    assert 'export const CONTROL_PROTOCOL_VERSION = "1.0"' in generated


def test_committed_protocol_artifacts_are_fresh() -> None:
    assert export_protocol(Path("protocol"), check=True) == 0


def test_protocol_artifacts_contain_no_machine_specific_values() -> None:
    content = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("protocol").iterdir()
        if path.is_file()
    )

    assert "Naresh Suthar" not in content
    assert "schema-only-token" not in content
    assert "AZURE_OPENAI_API_KEY" not in content


def test_freshness_check_reports_modified_artifact(tmp_path: Path) -> None:
    output = tmp_path / "protocol"
    export_protocol(output)
    target = output / "agentbus-v1.schema.json"
    target.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="stale"):
        export_protocol(output, check=True)


def test_openapi_is_deterministic_json() -> None:
    first = json.dumps(build_openapi(), sort_keys=True)
    second = json.dumps(build_openapi(), sort_keys=True)

    assert first == second
