import json
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.trace import (
    ContentAddressedStore,
    RuntimeTrace,
    TraceArchiveConsentRequiredError,
    TraceArchiveError,
    TraceArchiveExporter,
    TraceArchiveImporter,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.archive import _archive_root_values
from agentbus.trace.errors import TraceIntegrityError
from agentbus.trace.sealing import seal_run_provenance


def _archive(tmp_path, *, include_source=True):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_store = StateStore(tmp_path / "state.db")
    state_store.create_run(
        RunRecord(
            run_id="run-import",
            original_task="Import trace",
            model="deterministic",
            workspace=str(workspace),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        state_store,
        "run-import",
        object_root=tmp_path / "source-objects",
        workspace=workspace,
    )
    span = runtime.start_span(
        TraceSpanType.PROVIDER_RESPONSE,
        "captured response",
        attributes={
            "provider": "deterministic",
            "model": "fixture-v1",
            "role": "coder",
        },
    )
    metadata = runtime.object_store.put_json(
        {"code": "result = 42"},
        producing_span_id=span.span_id,
        media_type="application/vnd.agentbus.model-envelope+json",
    )
    output = runtime.object_store.reference_output(
        metadata,
        reference_id="captured-model",
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
        final_repository_tree_sha256="e" * 64,
    )
    archive = tmp_path / "trace.agentbus-trace"
    TraceArchiveExporter(runtime.object_store).export(
        trace,
        provenance,
        archive,
        include_source_content=include_source,
    )
    return archive, metadata


def _rewrite_archive(source, destination, *, mutate=None, extra=None):
    mutate = mutate or {}
    with ZipFile(source) as original:
        entries = [
            (info, mutate.get(info.filename, original.read(info.filename)))
            for info in original.infolist()
        ]
    with ZipFile(destination, mode="w", compression=ZIP_STORED) as rewritten:
        for original_info, payload in entries:
            info = ZipInfo(original_info.filename, original_info.date_time)
            info.compress_type = original_info.compress_type
            info.create_system = original_info.create_system
            info.external_attr = original_info.external_attr
            rewritten.writestr(info, payload)
        if extra is not None:
            rewritten.writestr(*extra)


def test_source_archive_requires_consent_before_any_object_is_written(
    tmp_path,
) -> None:
    archive, metadata = _archive(tmp_path)
    destination = ContentAddressedStore(tmp_path / "imported-objects")
    importer = TraceArchiveImporter(destination)

    inspected = importer.inspect(archive)
    assert inspected.objects_imported is False
    assert inspected.available_object_hashes == [metadata.sha256]
    assert destination.list_metadata() == []

    with pytest.raises(
        TraceArchiveConsentRequiredError,
        match="consent",
    ):
        importer.import_archive(archive)
    assert destination.list_metadata() == []

    imported = importer.import_archive(
        archive,
        allow_source_content=True,
    )
    assert imported.objects_imported is True
    assert destination.get(metadata.sha256).metadata.sha256 == metadata.sha256


def test_source_omitted_archive_imports_as_partial_without_consent(
    tmp_path,
) -> None:
    archive, metadata = _archive(tmp_path, include_source=False)
    destination = ContentAddressedStore(tmp_path / "imported-objects")

    imported = TraceArchiveImporter(destination).import_archive(archive)

    assert imported.objects_imported is True
    assert imported.available_object_hashes == []
    assert imported.missing_object_hashes == [metadata.sha256]
    assert destination.list_metadata() == []


def test_import_derives_source_consent_instead_of_trusting_manifest(
    tmp_path,
) -> None:
    archive, metadata = _archive(tmp_path)
    forged = tmp_path / "forged-source-marker.agentbus-trace"
    with ZipFile(archive) as source:
        manifest = json.loads(source.read("manifest.json"))
    for entry in manifest["entries"]:
        if entry["path"] == f"objects/{metadata.sha256}.blob":
            entry["source_content"] = False
    manifest["source_content_included"] = False
    manifest["source_content_warning"] = None
    manifest["archive_root"] = _archive_root_values(manifest)
    _rewrite_archive(
        archive,
        forged,
        mutate={
            "manifest.json": json.dumps(
                manifest,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        },
    )

    with pytest.raises(TraceIntegrityError, match="source-content marker"):
        TraceArchiveImporter(
            ContentAddressedStore(tmp_path / "forged-objects")
        ).inspect(forged)


def test_import_rejects_tampered_object_before_writing(tmp_path) -> None:
    archive, metadata = _archive(tmp_path)
    tampered = tmp_path / "tampered.agentbus-trace"
    _rewrite_archive(
        archive,
        tampered,
        mutate={f"objects/{metadata.sha256}.blob": b"tampered"},
    )
    destination = ContentAddressedStore(tmp_path / "imported-objects")

    with pytest.raises(TraceIntegrityError, match="integrity"):
        TraceArchiveImporter(destination).import_archive(
            tampered,
            allow_source_content=True,
        )

    assert destination.list_metadata() == []


def test_import_rejects_duplicate_and_traversal_entries(tmp_path) -> None:
    archive, _ = _archive(tmp_path)
    duplicate = tmp_path / "duplicate.agentbus-trace"
    with ZipFile(archive) as source:
        entries = [
            (info.filename, source.read(info.filename))
            for info in source.infolist()
        ]
    with ZipFile(duplicate, mode="w") as target:
        for name, payload in entries:
            target.writestr(name, payload)
        with pytest.warns(UserWarning, match="Duplicate name"):
            target.writestr(entries[0][0], entries[0][1])

    with pytest.raises(TraceArchiveError, match="duplicate"):
        TraceArchiveImporter(
            ContentAddressedStore(tmp_path / "duplicate-objects")
        ).inspect(duplicate)

    traversal = tmp_path / "traversal.agentbus-trace"
    _rewrite_archive(
        archive,
        traversal,
        extra=("../outside.txt", b"escape"),
    )
    outside = tmp_path / "outside.txt"
    with pytest.raises(TraceArchiveError, match="unsafe entry path"):
        TraceArchiveImporter(
            ContentAddressedStore(tmp_path / "traversal-objects")
        ).inspect(traversal)
    assert not outside.exists()


def test_import_rejects_executable_entries_and_compression_bombs(
    tmp_path,
) -> None:
    executable = tmp_path / "executable.agentbus-trace"
    with ZipFile(executable, mode="w") as archive:
        info = ZipInfo("payload.bin")
        info.create_system = 3
        info.external_attr = (0o100700) << 16
        archive.writestr(info, b"not executable")

    with pytest.raises(TraceArchiveError, match="executable"):
        TraceArchiveImporter(
            ContentAddressedStore(tmp_path / "executable-objects")
        ).inspect(executable)

    bomb = tmp_path / "bomb.agentbus-trace"
    with ZipFile(bomb, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("payload.bin", b"0" * (2 * 1024 * 1024))

    with pytest.raises(TraceArchiveError, match="compression-ratio"):
        TraceArchiveImporter(
            ContentAddressedStore(tmp_path / "bomb-objects")
        ).inspect(bomb)
