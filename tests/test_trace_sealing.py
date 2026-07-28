from pathlib import Path

import pytest

from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.trace import RuntimeTrace, TraceSpanType, TraceStatus
from agentbus.trace.errors import TraceIntegrityError
from agentbus.trace.sealing import seal_run_provenance


def _runtime(tmp_path: Path, run_id: str = "run-seal"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    store = StateStore(tmp_path / "state.db")
    store.create_run(
        RunRecord(
            run_id=run_id,
            original_task="Seal this run",
            model="deterministic",
            workspace=str(workspace),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        store,
        run_id,
        object_root=tmp_path / "objects",
        workspace=workspace,
    )
    return store, runtime


def test_sealer_builds_and_persists_verified_runtime_provenance(tmp_path) -> None:
    store, runtime = _runtime(tmp_path)
    provider = runtime.start_span(
        TraceSpanType.PROVIDER_RESPONSE,
        "deterministic response",
        attributes={
            "provider": "deterministic",
            "model": "fixture-v1",
            "role": "coder",
        },
    )
    response = runtime.capture_json_output(
        provider,
        "model.response",
        {"summary": "safe structured response"},
    )
    runtime.finish_span(
        provider,
        output_references=[response] if response is not None else [],
    )
    tool = runtime.start_span(
        TraceSpanType.TOOL_INVOCATION,
        "filesystem.read",
        attributes={
            "tool_name": "filesystem.read",
            "tool_version": {"major": 1, "minor": 2, "patch": 3},
            "descriptor_protocol_version": "1.0",
        },
    )
    runtime.finish_span(tool)
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)

    manifest = seal_run_provenance(
        trace,
        state_store=store,
        object_store=runtime.object_store,
        configuration={"provider": "deterministic", "api_key": "not-stored"},
        task_graph={"version": 1, "tasks": []},
        final_repository_tree_sha256="b" * 64,
    )

    assert store.get_run_provenance_manifest(trace.run_id) == manifest
    assert manifest.integrity_root != "0" * 64
    assert manifest.final_repository_tree_sha256 == "b" * 64
    assert manifest.provider_routes[0].provider == "deterministic"
    assert manifest.tool_descriptors[0].version == "1.2.3"
    assert response.sha256 in manifest.output_object_hashes
    assert "not-stored" not in manifest.model_dump_json()


def test_sealer_reports_tampered_referenced_objects_as_integrity_failure(
    tmp_path,
) -> None:
    store, runtime = _runtime(tmp_path, "run-tampered")
    span = runtime.start_span(TraceSpanType.CUSTOM, "captured output")
    output = runtime.capture_json_output(span, "result", {"value": 1})
    runtime.finish_span(
        span,
        output_references=[output] if output is not None else [],
    )
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)
    assert output is not None
    blob_path, _ = runtime.object_store._object_paths(output.sha256)
    blob_path.write_bytes(b"tampered")

    with pytest.raises(TraceIntegrityError, match="integrity"):
        seal_run_provenance(
            trace,
            state_store=store,
            object_store=runtime.object_store,
            configuration={"provider": "deterministic"},
            task_graph={"version": 1, "tasks": []},
            final_repository_tree_sha256="c" * 64,
        )

    assert store.find_run_provenance_manifest(trace.run_id) is None
