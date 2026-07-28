from zipfile import ZipFile

import pytest

from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.trace import (
    RuntimeTrace,
    TraceArchiveExporter,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.errors import TraceStorageError
from agentbus.trace.sealing import seal_run_provenance


def _sealed_source_trace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_store = StateStore(tmp_path / "state.db")
    state_store.create_run(
        RunRecord(
            run_id="run-archive",
            original_task="Export trace",
            model="deterministic",
            workspace=str(workspace),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        state_store,
        "run-archive",
        object_root=tmp_path / "objects",
        workspace=workspace,
    )
    span = runtime.start_span(
        TraceSpanType.PROVIDER_RESPONSE,
        "captured provider response",
        attributes={
            "provider": "deterministic",
            "model": "fixture-v1",
            "role": "coder",
        },
    )
    metadata = runtime.object_store.put_json(
        {
            "code": "def add(a, b): return a + b",
            "api_key": "must-not-export",
        },
        producing_span_id=span.span_id,
        media_type="application/vnd.agentbus.model-envelope+json",
    )
    output = runtime.object_store.reference_output(
        metadata,
        reference_id="model-output",
        name="model.response",
    )
    runtime.finish_span(span, output_references=[output])
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)
    provenance = seal_run_provenance(
        trace,
        state_store=state_store,
        object_store=runtime.object_store,
        configuration={"provider": "deterministic"},
        task_graph={"version": 1, "tasks": []},
        final_repository_tree_sha256="d" * 64,
    )
    return runtime.object_store, trace, provenance, metadata


def test_trace_archive_export_is_byte_deterministic_and_non_executable(
    tmp_path,
) -> None:
    store, trace, provenance, metadata = _sealed_source_trace(tmp_path)
    exporter = TraceArchiveExporter(store)
    first = tmp_path / "first.agentbus-trace"
    second = tmp_path / "second.agentbus-trace"

    manifest = exporter.export(
        trace,
        provenance,
        first,
        assertions={"final_status": "succeeded"},
        include_source_content=True,
    )
    exporter.export(
        trace,
        provenance,
        second,
        assertions={"final_status": "succeeded"},
        include_source_content=True,
    )

    assert first.read_bytes() == second.read_bytes()
    assert manifest.source_content_included is True
    assert manifest.included_object_hashes == [metadata.sha256]
    with ZipFile(first) as archive:
        infos = archive.infolist()
        assert [info.filename for info in infos] == sorted(
            info.filename for info in infos
        )
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos)
        assert all(not ((info.external_attr >> 16) & 0o111) for info in infos)
        assert b"must-not-export" not in archive.read(
            f"objects/{metadata.sha256}.blob"
        )


def test_trace_archive_omits_source_like_objects_without_explicit_opt_in(
    tmp_path,
) -> None:
    store, trace, provenance, metadata = _sealed_source_trace(tmp_path)
    destination = tmp_path / "safe.agentbus-trace"

    manifest = TraceArchiveExporter(store).export(
        trace,
        provenance,
        destination,
    )

    assert manifest.source_content_included is False
    assert manifest.included_object_hashes == []
    assert manifest.omitted_source_hashes == [metadata.sha256]
    with ZipFile(destination) as archive:
        assert f"objects/{metadata.sha256}.blob" not in archive.namelist()


def test_trace_archive_refuses_to_replace_an_existing_destination(
    tmp_path,
) -> None:
    store, trace, provenance, _ = _sealed_source_trace(tmp_path)
    destination = tmp_path / "existing.agentbus-trace"
    destination.write_bytes(b"user-owned")

    with pytest.raises(TraceStorageError, match="already exists"):
        TraceArchiveExporter(store).export(
            trace,
            provenance,
            destination,
        )

    assert destination.read_bytes() == b"user-owned"
