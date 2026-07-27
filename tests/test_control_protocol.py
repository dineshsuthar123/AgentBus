from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentbus.control.models import ReplayCreateRequest
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


def test_cancellation_lifecycle_is_backward_compatible_protocol_extension() -> None:
    schema = build_json_schema()
    lifecycle = schema["$defs"]["CancellationLifecycle"]["properties"]
    run_summary = schema["$defs"]["RunSummary"]

    assert "cancellation" in run_summary["properties"]
    assert "cancellation" not in run_summary.get("required", [])
    assert {
        "requested",
        "acknowledged",
        "acknowledgement_stage",
        "active_non_interruptible_operation",
        "tasks_prevented_from_starting",
        "tasks_completed_after_request",
        "resume_eligible",
        "terminal_reason",
    } <= lifecycle.keys()


def test_trace_and_replay_models_are_bounded_protocol_extensions() -> None:
    definitions = build_json_schema()["$defs"]

    assert {
        "TraceResponse",
        "TraceSpanListResponse",
        "TraceSpanDetailResponse",
        "ProvenanceResponse",
        "RunReplayabilityResponse",
        "ReplayCreateRequest",
        "ReplaySessionResponse",
        "ComparisonResponse",
        "TraceArchiveImportRequest",
        "TraceArchiveExportResponse",
    } <= definitions.keys()
    assert (
        definitions["TraceSpanListResponse"]["properties"]["spans"]["maxItems"]
        == 500
    )
    replay_properties = definitions["ReplaySessionResponse"]["properties"]
    assert "isolated_workspace" not in replay_properties
    assert "isolated" in replay_properties
    assert (
        definitions["TraceArchiveImportRequest"]["properties"]["archive_base64"][
            "maxLength"
        ]
        == 900_000
    )


def test_replay_protocol_requires_explicit_consistent_mode() -> None:
    with pytest.raises(ValidationError, match="mode"):
        ReplayCreateRequest.model_validate({})
    with pytest.raises(ValidationError, match="choose either"):
        ReplayCreateRequest(
            mode="offline",
            from_span_id="span-1",
            from_checkpoint_id="checkpoint-1",
        )
    with pytest.raises(ValidationError, match="fork=true"):
        ReplayCreateRequest(
            mode="offline",
            changed_inputs={"task": "changed"},
        )


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
