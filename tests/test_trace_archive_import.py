import hashlib
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
from agentbus.trace.redaction import canonical_json_bytes
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


def _rewrite_archive(
    source,
    destination,
    *,
    mutate=None,
    extra=None,
    remove=(),
):
    mutate = mutate or {}
    removed = set(remove)
    with ZipFile(source) as original:
        entries = [
            (info, mutate.get(info.filename, original.read(info.filename)))
            for info in original.infolist()
            if info.filename not in removed
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


def _manifest_payload(manifest):
    manifest["archive_root"] = _archive_root_values(manifest)
    return canonical_json_bytes(manifest)


def _replace_manifest_entry(manifest, path, payload):
    entry = next(item for item in manifest["entries"] if item["path"] == path)
    entry["sha256"] = hashlib.sha256(payload).hexdigest()
    entry["byte_size"] = len(payload)


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


def test_import_rejects_absent_object_and_malformed_manifest_before_writing(
    tmp_path,
) -> None:
    archive, metadata = _archive(tmp_path)
    absent = tmp_path / "absent-object.agentbus-trace"
    _rewrite_archive(
        archive,
        absent,
        remove=(f"objects/{metadata.sha256}.blob",),
    )
    absent_store = ContentAddressedStore(tmp_path / "absent-objects")

    with pytest.raises(TraceArchiveError, match="exactly match"):
        TraceArchiveImporter(absent_store).import_archive(
            absent,
            allow_source_content=True,
        )
    assert absent_store.list_metadata() == []

    malformed = tmp_path / "malformed-manifest.agentbus-trace"
    _rewrite_archive(
        archive,
        malformed,
        mutate={"manifest.json": b"{not-valid-json"},
    )
    malformed_store = ContentAddressedStore(tmp_path / "malformed-objects")

    with pytest.raises(TraceIntegrityError, match="manifest is invalid"):
        TraceArchiveImporter(malformed_store).import_archive(
            malformed,
            allow_source_content=True,
        )
    assert malformed_store.list_metadata() == []


def test_import_rejects_forged_object_hash_and_metadata_identity(
    tmp_path,
) -> None:
    archive, metadata = _archive(tmp_path)
    blob_path = f"objects/{metadata.sha256}.blob"
    metadata_path = f"objects/{metadata.sha256}.metadata.json"
    with ZipFile(archive) as source:
        manifest = json.loads(source.read("manifest.json"))
        metadata_document = json.loads(source.read(metadata_path))

    altered_payload = b"altered but manifest-bound content"
    altered_manifest = json.loads(json.dumps(manifest))
    _replace_manifest_entry(altered_manifest, blob_path, altered_payload)
    altered = tmp_path / "forged-object-hash.agentbus-trace"
    _rewrite_archive(
        archive,
        altered,
        mutate={
            blob_path: altered_payload,
            "manifest.json": _manifest_payload(altered_manifest),
        },
    )
    altered_store = ContentAddressedStore(tmp_path / "forged-hash-objects")

    with pytest.raises(TraceIntegrityError, match="failed validation"):
        TraceArchiveImporter(altered_store).import_archive(
            altered,
            allow_source_content=True,
        )
    assert altered_store.list_metadata() == []

    metadata_document["sha256"] = "f" * 64
    forged_metadata = canonical_json_bytes(metadata_document)
    metadata_manifest = json.loads(json.dumps(manifest))
    _replace_manifest_entry(
        metadata_manifest,
        metadata_path,
        forged_metadata,
    )
    mismatched = tmp_path / "forged-metadata-identity.agentbus-trace"
    _rewrite_archive(
        archive,
        mismatched,
        mutate={
            metadata_path: forged_metadata,
            "manifest.json": _manifest_payload(metadata_manifest),
        },
    )
    metadata_store = ContentAddressedStore(tmp_path / "forged-metadata-objects")

    with pytest.raises(TraceIntegrityError, match="failed validation"):
        TraceArchiveImporter(metadata_store).import_archive(
            mismatched,
            allow_source_content=True,
        )
    assert metadata_store.list_metadata() == []


def test_import_rejects_stale_and_unbound_protocol_documents(tmp_path) -> None:
    archive, _ = _archive(tmp_path)
    with ZipFile(archive) as source:
        manifest = json.loads(source.read("manifest.json"))
        protocol_path = next(
            item["path"]
            for item in manifest["entries"]
            if item["path"].startswith("protocols/")
        )

    stale_payload = canonical_json_bytes({"schema_version": 999})
    stale_manifest = json.loads(json.dumps(manifest))
    _replace_manifest_entry(stale_manifest, protocol_path, stale_payload)
    stale = tmp_path / "stale-protocol.agentbus-trace"
    _rewrite_archive(
        archive,
        stale,
        mutate={
            protocol_path: stale_payload,
            "manifest.json": _manifest_payload(stale_manifest),
        },
    )
    stale_store = ContentAddressedStore(tmp_path / "stale-protocol-objects")

    with pytest.raises(TraceIntegrityError, match="drifted"):
        TraceArchiveImporter(stale_store).import_archive(
            stale,
            allow_source_content=True,
        )
    assert stale_store.list_metadata() == []

    unbound_path = "protocols/unbound-fixture.json"
    unbound_payload = canonical_json_bytes({"schema_version": 1})
    unbound_manifest = json.loads(json.dumps(manifest))
    unbound_manifest["entries"].append(
        {
            "path": unbound_path,
            "sha256": hashlib.sha256(unbound_payload).hexdigest(),
            "byte_size": len(unbound_payload),
            "media_type": "application/schema+json",
            "source_content": False,
        }
    )
    unbound_manifest["entries"].sort(key=lambda item: item["path"])
    unbound = tmp_path / "unbound-protocol.agentbus-trace"
    _rewrite_archive(
        archive,
        unbound,
        mutate={"manifest.json": _manifest_payload(unbound_manifest)},
        extra=(unbound_path, unbound_payload),
    )
    unbound_store = ContentAddressedStore(tmp_path / "unbound-protocol-objects")

    with pytest.raises(TraceArchiveError, match="protocol"):
        TraceArchiveImporter(unbound_store).import_archive(
            unbound,
            allow_source_content=True,
        )
    assert unbound_store.list_metadata() == []


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

    duplicate_store = ContentAddressedStore(tmp_path / "duplicate-objects")
    with pytest.raises(TraceArchiveError, match="duplicate"):
        TraceArchiveImporter(duplicate_store).inspect(duplicate)
    assert duplicate_store.list_metadata() == []

    traversal = tmp_path / "traversal.agentbus-trace"
    _rewrite_archive(
        archive,
        traversal,
        extra=("../outside.txt", b"escape"),
    )
    outside = tmp_path / "outside.txt"
    traversal_store = ContentAddressedStore(tmp_path / "traversal-objects")
    with pytest.raises(TraceArchiveError, match="unsafe entry path"):
        TraceArchiveImporter(traversal_store).inspect(traversal)
    assert not outside.exists()
    assert traversal_store.list_metadata() == []


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

    bomb_store = ContentAddressedStore(tmp_path / "bomb-objects")
    with pytest.raises(TraceArchiveError, match="compression-ratio"):
        TraceArchiveImporter(bomb_store).inspect(bomb)
    assert bomb_store.list_metadata() == []
