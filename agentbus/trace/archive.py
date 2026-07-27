from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zipfile import (
    ZIP_DEFLATED,
    ZIP_STORED,
    BadZipFile,
    ZipFile,
    ZipInfo,
)

from pydantic import Field, field_validator, model_validator

from agentbus.trace.blobs import BlobMetadata, RetentionClass
from agentbus.trace.errors import (
    TraceArchiveConsentRequiredError,
    TraceArchiveError,
    TraceIntegrityError,
    TraceStorageError,
)
from agentbus.trace.models import (
    MAX_TRACE_ITEMS,
    Sha256Digest,
    Trace,
    TraceIdentifier,
    TraceModel,
)
from agentbus.trace.provenance import (
    ProvenanceManifest,
    verify_provenance_core,
)
from agentbus.trace.protocols import provenance_protocol_documents
from agentbus.trace.redaction import (
    canonical_json_bytes,
    contains_secret_material,
    sanitize_document,
)
from agentbus.trace.storage import ContentAddressedStore

TRACE_ARCHIVE_SCHEMA_VERSION = 1
TRACE_ARCHIVE_FORMAT = "agentbus.trace-archive"
MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 100

_ARCHIVE_MANIFEST_PATH = "manifest.json"
_FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_SAFE_PROTOCOL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SOURCE_MEDIA_MARKERS = (
    "diff",
    "model-envelope",
    "patch",
    "source",
    "tool-envelope",
)


class TraceArchiveEntry(TraceModel):
    path: str = Field(min_length=1, max_length=512)
    sha256: Sha256Digest
    byte_size: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=200)
    source_content: bool = False

    @field_validator("path")
    @classmethod
    def path_is_portable(cls, value: str) -> str:
        if (
            "\\" in value
            or value.startswith("/")
            or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("trace archive entry path is unsafe")
        return value


class TraceArchiveManifest(TraceModel):
    schema_version: int = TRACE_ARCHIVE_SCHEMA_VERSION
    format_name: str = TRACE_ARCHIVE_FORMAT
    trace_id: TraceIdentifier
    run_id: TraceIdentifier
    created_at: datetime
    provenance_root: Sha256Digest
    entries: list[TraceArchiveEntry] = Field(
        min_length=3,
        max_length=MAX_ARCHIVE_ENTRIES,
    )
    included_object_hashes: list[Sha256Digest] = Field(default_factory=list)
    omitted_source_hashes: list[Sha256Digest] = Field(default_factory=list)
    source_content_included: bool = False
    source_content_warning: str | None = Field(default=None, max_length=512)
    archive_root: Sha256Digest

    @field_validator("schema_version")
    @classmethod
    def schema_is_supported(cls, value: int) -> int:
        if value != TRACE_ARCHIVE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported trace archive schema version: {value}"
            )
        return value

    @field_validator("format_name")
    @classmethod
    def format_is_supported(cls, value: str) -> str:
        if value != TRACE_ARCHIVE_FORMAT:
            raise ValueError(f"unsupported trace archive format: {value}")
        return value

    @field_validator("included_object_hashes", "omitted_source_hashes")
    @classmethod
    def hashes_are_sorted_unique(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("archive object hashes must be sorted and unique")
        return value

    @model_validator(mode="after")
    def archive_integrity_is_valid(self) -> "TraceArchiveManifest":
        paths = [entry.path for entry in self.entries]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("trace archive entries must be sorted and unique")
        if (
            set(self.included_object_hashes)
            & set(self.omitted_source_hashes)
        ):
            raise ValueError(
                "included and omitted archive objects cannot overlap"
            )
        source_entries = any(entry.source_content for entry in self.entries)
        if self.source_content_included != source_entries:
            raise ValueError(
                "archive source-content marker does not match its entries"
            )
        if self.source_content_included and not self.source_content_warning:
            raise ValueError(
                "archives containing source content require a warning"
            )
        if self.archive_root != _archive_root(self):
            raise ValueError("trace archive integrity root does not match")
        return self


class ImportedTraceArchive(TraceModel):
    manifest: TraceArchiveManifest
    trace: Trace
    provenance: ProvenanceManifest
    assertions: dict[str, Any] = Field(default_factory=dict)
    protocol_documents: dict[str, Any] = Field(default_factory=dict)
    available_object_hashes: list[Sha256Digest] = Field(default_factory=list)
    missing_object_hashes: list[Sha256Digest] = Field(default_factory=list)
    objects_imported: bool = False

    @field_validator("assertions", "protocol_documents")
    @classmethod
    def documents_are_sanitized(cls, value: dict[str, Any]) -> dict[str, Any]:
        return sanitize_document(value).value


@dataclass(frozen=True)
class _ValidatedArchive:
    imported: ImportedTraceArchive
    objects: dict[str, tuple[BlobMetadata, bytes]]


class TraceArchiveExporter:
    def __init__(
        self,
        object_store: ContentAddressedStore,
        *,
        max_entries: int = MAX_ARCHIVE_ENTRIES,
        max_uncompressed_bytes: int = MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    ) -> None:
        if max_entries < 3 or max_entries > MAX_ARCHIVE_ENTRIES:
            raise ValueError("trace archive entry limit is invalid")
        if (
            max_uncompressed_bytes < 1
            or max_uncompressed_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES
        ):
            raise ValueError("trace archive size limit is invalid")
        self.object_store = object_store
        self.max_entries = max_entries
        self.max_uncompressed_bytes = max_uncompressed_bytes

    def export(
        self,
        trace: Trace,
        provenance: ProvenanceManifest,
        destination: str | Path,
        *,
        assertions: Mapping[str, Any] | None = None,
        protocol_documents: Mapping[str, Any] | None = None,
        include_source_content: bool = False,
    ) -> TraceArchiveManifest:
        verify_provenance_core(provenance, trace)
        if trace.completed_at is None:
            raise TraceStorageError(
                "Only terminal execution traces can be exported."
            )
        if (
            provenance.trace_id != trace.trace_id
            or provenance.run_id != trace.run_id
        ):
            raise TraceIntegrityError(
                "Trace archive provenance identifies a different run."
            )
        files: dict[str, tuple[bytes, str, bool]] = {}
        files["assertions.json"] = (
            sanitize_document(dict(assertions or {})).canonical_bytes,
            "application/json",
            False,
        )
        files["provenance.json"] = (
            canonical_json_bytes(provenance.model_dump(mode="json")),
            "application/vnd.agentbus.provenance+json",
            False,
        )
        files["trace.json"] = (
            canonical_json_bytes(trace.model_dump(mode="json")),
            "application/vnd.agentbus.trace+json",
            False,
        )
        protocols = (
            dict(protocol_documents)
            if protocol_documents is not None
            else provenance_protocol_documents()
        )
        for name, document in sorted(protocols.items()):
            if not _SAFE_PROTOCOL_NAME.fullmatch(name):
                raise TraceStorageError(
                    "Trace archive protocol name is unsafe."
                )
            payload = sanitize_document(document).canonical_bytes
            expected = provenance.protocol_hashes.get(name)
            actual = hashlib.sha256(payload).hexdigest()
            if expected is not None and actual != expected:
                raise TraceIntegrityError(
                    f"Protocol document '{name}' does not match provenance."
                )
            files[f"protocols/{name}.json"] = (
                payload,
                "application/schema+json",
                False,
            )

        names_by_hash = _reference_names(trace)
        included_hashes: list[str] = []
        omitted_hashes: list[str] = []
        source_included = False
        for digest in _provenance_blob_hashes(provenance):
            stored = self.object_store.get(digest)
            source_content = _is_potential_source_content(
                stored.metadata,
                names_by_hash.get(digest, set()),
            )
            if source_content and not include_source_content:
                omitted_hashes.append(digest)
                continue
            included_hashes.append(digest)
            source_included = source_included or source_content
            files[f"objects/{digest}.blob"] = (
                stored.data,
                stored.metadata.media_type,
                source_content,
            )
            files[f"objects/{digest}.metadata.json"] = (
                canonical_json_bytes(
                    stored.metadata.model_dump(mode="json")
                ),
                "application/vnd.agentbus.trace-blob-metadata+json",
                False,
            )

        entries = [
            TraceArchiveEntry(
                path=path,
                sha256=hashlib.sha256(payload).hexdigest(),
                byte_size=len(payload),
                media_type=media_type,
                source_content=source,
            )
            for path, (payload, media_type, source) in sorted(files.items())
        ]
        if len(entries) + 1 > self.max_entries:
            raise TraceStorageError(
                "Trace archive exceeds its configured entry bound."
            )
        total_bytes = sum(entry.byte_size for entry in entries)
        if total_bytes > self.max_uncompressed_bytes:
            raise TraceStorageError(
                "Trace archive exceeds its configured size bound."
            )
        manifest_values = {
            "trace_id": trace.trace_id,
            "run_id": trace.run_id,
            "created_at": trace.completed_at,
            "provenance_root": provenance.integrity_root,
            "entries": entries,
            "included_object_hashes": sorted(included_hashes),
            "omitted_source_hashes": sorted(omitted_hashes),
            "source_content_included": source_included,
            "source_content_warning": (
                "This archive contains sanitized captured source-like content; "
                "review its origin and license before importing."
                if source_included
                else None
            ),
        }
        manifest = TraceArchiveManifest(
            **manifest_values,
            archive_root=_archive_root_values(manifest_values),
        )
        files[_ARCHIVE_MANIFEST_PATH] = (
            canonical_json_bytes(manifest.model_dump(mode="json")),
            "application/vnd.agentbus.trace-archive-manifest+json",
            False,
        )
        _write_deterministic_zip(
            destination,
            {path: value[0] for path, value in files.items()},
        )
        return manifest


class TraceArchiveImporter:
    def __init__(
        self,
        object_store: ContentAddressedStore,
        *,
        max_entries: int = MAX_ARCHIVE_ENTRIES,
        max_uncompressed_bytes: int = MAX_ARCHIVE_UNCOMPRESSED_BYTES,
        max_compression_ratio: int = MAX_ARCHIVE_COMPRESSION_RATIO,
    ) -> None:
        if max_entries < 3 or max_entries > MAX_ARCHIVE_ENTRIES:
            raise ValueError("trace archive entry limit is invalid")
        if (
            max_uncompressed_bytes < 1
            or max_uncompressed_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES
        ):
            raise ValueError("trace archive size limit is invalid")
        if (
            max_compression_ratio < 1
            or max_compression_ratio > MAX_ARCHIVE_COMPRESSION_RATIO
        ):
            raise ValueError("trace archive compression ratio is invalid")
        self.object_store = object_store
        self.max_entries = max_entries
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_compression_ratio = max_compression_ratio

    def inspect(self, source: str | Path) -> ImportedTraceArchive:
        return self._validate(source).imported

    def import_archive(
        self,
        source: str | Path,
        *,
        allow_source_content: bool = False,
    ) -> ImportedTraceArchive:
        validated = self._validate(source)
        imported = validated.imported
        if (
            imported.manifest.source_content_included
            and not allow_source_content
        ):
            raise TraceArchiveConsentRequiredError(
                "Trace archive contains source-like content; explicit import "
                "consent is required."
            )
        stored_hashes: list[str] = []
        for digest in imported.available_object_hashes:
            metadata, data = validated.objects[digest]
            stored = self.object_store.put_bytes(
                data,
                producing_span_id=metadata.producing_span_ids[0],
                media_type=metadata.media_type,
                retention_class=RetentionClass.FIXTURE,
                redaction=metadata.redaction,
            )
            if stored.sha256 != digest:
                raise TraceIntegrityError(
                    "Imported trace object changed while being persisted."
                )
            stored_hashes.append(digest)
        return ImportedTraceArchive.model_validate(
            imported.model_copy(
                update={
                    "available_object_hashes": stored_hashes,
                    "objects_imported": True,
                }
            ).model_dump()
        )

    def _validate(self, source: str | Path) -> _ValidatedArchive:
        files = self._read_archive(source)
        manifest_payload = files.get(_ARCHIVE_MANIFEST_PATH)
        if manifest_payload is None:
            raise TraceArchiveError(
                "Trace archive is missing its manifest."
            )
        if contains_secret_material(manifest_payload):
            raise TraceArchiveError(
                "Trace archive manifest contains secret material."
            )
        try:
            manifest = TraceArchiveManifest.model_validate_json(
                manifest_payload
            )
        except Exception as exc:
            raise TraceIntegrityError(
                "Trace archive manifest is invalid."
            ) from exc
        expected_paths = {
            _ARCHIVE_MANIFEST_PATH,
            *(entry.path for entry in manifest.entries),
        }
        if set(files) != expected_paths:
            raise TraceArchiveError(
                "Trace archive entries do not exactly match its manifest."
            )
        entry_by_path = {entry.path: entry for entry in manifest.entries}
        for path, entry in entry_by_path.items():
            payload = files[path]
            if (
                len(payload) != entry.byte_size
                or hashlib.sha256(payload).hexdigest() != entry.sha256
            ):
                raise TraceIntegrityError(
                    f"Trace archive entry '{path}' failed integrity validation."
                )
            if contains_secret_material(payload):
                raise TraceArchiveError(
                    f"Trace archive entry '{path}' contains secret material."
                )
        required = {"assertions.json", "provenance.json", "trace.json"}
        if not required <= set(entry_by_path):
            raise TraceArchiveError(
                "Trace archive is missing required documents."
            )
        try:
            trace = Trace.model_validate_json(files["trace.json"])
            provenance = ProvenanceManifest.model_validate_json(
                files["provenance.json"]
            )
            assertions = _json_mapping(
                files["assertions.json"],
                "archive assertions",
            )
        except TraceArchiveError:
            raise
        except Exception as exc:
            raise TraceIntegrityError(
                "Trace archive contains invalid core documents."
            ) from exc
        if (
            trace.trace_id != manifest.trace_id
            or trace.run_id != manifest.run_id
            or provenance.integrity_root != manifest.provenance_root
        ):
            raise TraceIntegrityError(
                "Trace archive identities do not match its manifest."
            )
        verify_provenance_core(provenance, trace)
        protocols = self._validate_protocols(
            files,
            entry_by_path,
            provenance,
        )
        objects = self._validate_objects(
            files,
            entry_by_path,
            manifest,
            provenance,
            trace,
        )
        imported = ImportedTraceArchive(
            manifest=manifest,
            trace=trace,
            provenance=provenance,
            assertions=assertions,
            protocol_documents=protocols,
            available_object_hashes=sorted(objects),
            missing_object_hashes=manifest.omitted_source_hashes,
            objects_imported=False,
        )
        return _ValidatedArchive(imported=imported, objects=objects)

    def _read_archive(self, source: str | Path) -> dict[str, bytes]:
        requested = Path(source).expanduser()
        if requested.is_symlink():
            raise TraceArchiveError(
                "Trace archive path must not be a symbolic link."
            )
        try:
            path = requested.resolve(strict=True)
            archive_size = path.stat().st_size
        except (OSError, RuntimeError) as exc:
            raise TraceArchiveError("Trace archive is unavailable.") from exc
        if not path.is_file():
            raise TraceArchiveError("Trace archive must be a regular file.")
        if archive_size > self.max_uncompressed_bytes:
            raise TraceArchiveError(
                "Trace archive file exceeds its configured size bound."
            )
        try:
            with ZipFile(path, mode="r") as archive:
                infos = archive.infolist()
                if len(infos) > self.max_entries:
                    raise TraceArchiveError(
                        "Trace archive exceeds its configured entry bound."
                    )
                names = [info.filename for info in infos]
                if len(names) != len(set(names)):
                    raise TraceArchiveError(
                        "Trace archive contains duplicate entries."
                    )
                total = 0
                for info in infos:
                    _validate_zip_entry(
                        info,
                        max_uncompressed_bytes=self.max_uncompressed_bytes,
                        max_compression_ratio=self.max_compression_ratio,
                    )
                    total += info.file_size
                    if total > self.max_uncompressed_bytes:
                        raise TraceArchiveError(
                            "Trace archive exceeds its uncompressed size bound."
                        )
                return {
                    info.filename: _read_zip_entry(
                        archive,
                        info,
                        maximum=info.file_size,
                    )
                    for info in infos
                }
        except TraceArchiveError:
            raise
        except (BadZipFile, OSError, RuntimeError) as exc:
            raise TraceArchiveError(
                "Trace archive is not a valid bounded ZIP document."
            ) from exc

    @staticmethod
    def _validate_protocols(
        files: dict[str, bytes],
        entries: dict[str, TraceArchiveEntry],
        provenance: ProvenanceManifest,
    ) -> dict[str, Any]:
        protocols: dict[str, Any] = {}
        for path in sorted(entries):
            if not path.startswith("protocols/"):
                continue
            name = path.removeprefix("protocols/").removesuffix(".json")
            if (
                not path.endswith(".json")
                or not _SAFE_PROTOCOL_NAME.fullmatch(name)
            ):
                raise TraceArchiveError(
                    "Trace archive protocol entry is unsafe."
                )
            document = _json_mapping(files[path], f"protocol '{name}'")
            expected = provenance.protocol_hashes.get(name)
            actual = hashlib.sha256(
                canonical_json_bytes(document)
            ).hexdigest()
            if expected is not None and actual != expected:
                raise TraceIntegrityError(
                    f"Trace archive protocol '{name}' drifted."
                )
            protocols[name] = document
        missing = set(provenance.protocol_hashes) - set(protocols)
        if missing:
            raise TraceArchiveError(
                "Trace archive omits provenance protocol documents."
            )
        return protocols

    @staticmethod
    def _validate_objects(
        files: dict[str, bytes],
        entries: dict[str, TraceArchiveEntry],
        manifest: TraceArchiveManifest,
        provenance: ProvenanceManifest,
        trace: Trace,
    ) -> dict[str, tuple[BlobMetadata, bytes]]:
        objects: dict[str, tuple[BlobMetadata, bytes]] = {}
        names_by_hash = _reference_names(trace)
        for digest in manifest.included_object_hashes:
            blob_path = f"objects/{digest}.blob"
            metadata_path = f"objects/{digest}.metadata.json"
            if blob_path not in entries or metadata_path not in entries:
                raise TraceArchiveError(
                    f"Trace archive object '{digest}' is incomplete."
                )
            try:
                metadata = BlobMetadata.model_validate_json(
                    files[metadata_path]
                )
            except Exception as exc:
                raise TraceIntegrityError(
                    f"Trace archive object '{digest}' has invalid metadata."
                ) from exc
            data = files[blob_path]
            if (
                metadata.sha256 != digest
                or metadata.byte_size != len(data)
                or hashlib.sha256(data).hexdigest() != digest
                or entries[blob_path].media_type != metadata.media_type
            ):
                raise TraceIntegrityError(
                    f"Trace archive object '{digest}' failed validation."
                )
            source_content = _is_potential_source_content(
                metadata,
                names_by_hash.get(digest, set()),
            )
            if (
                entries[blob_path].source_content != source_content
                or entries[metadata_path].source_content
            ):
                raise TraceIntegrityError(
                    f"Trace archive object '{digest}' has an invalid "
                    "source-content marker."
                )
            objects[digest] = (metadata, data)
        object_paths = {
            path
            for path in entries
            if path.startswith("objects/")
        }
        expected_paths = {
            path
            for digest in manifest.included_object_hashes
            for path in (
                f"objects/{digest}.blob",
                f"objects/{digest}.metadata.json",
            )
        }
        if object_paths != expected_paths:
            raise TraceArchiveError(
                "Trace archive contains undeclared object entries."
            )
        provenance_hashes = set(_provenance_blob_hashes(provenance))
        declared_hashes = {
            *manifest.included_object_hashes,
            *manifest.omitted_source_hashes,
        }
        if provenance_hashes != declared_hashes:
            raise TraceIntegrityError(
                "Trace archive object inventory does not match provenance."
            )
        return objects


def _archive_root(manifest: TraceArchiveManifest) -> str:
    return _archive_root_values(
        manifest.model_dump(mode="json", exclude={"archive_root"})
    )


def _archive_root_values(values: Mapping[str, Any]) -> str:
    payload = dict(values)
    payload.pop("archive_root", None)
    payload.setdefault("schema_version", TRACE_ARCHIVE_SCHEMA_VERSION)
    payload.setdefault("format_name", TRACE_ARCHIVE_FORMAT)
    if isinstance(payload.get("created_at"), datetime):
        payload["created_at"] = payload["created_at"].isoformat().replace(
            "+00:00",
            "Z",
        )
    payload["entries"] = [
        entry.model_dump(mode="json")
        if isinstance(entry, TraceArchiveEntry)
        else entry
        for entry in payload.get("entries", [])
    ]
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _provenance_blob_hashes(provenance: ProvenanceManifest) -> list[str]:
    hashes = {
        entry.identifier
        for entry in provenance.integrity_entries
        if entry.kind == "blob"
    }
    if len(hashes) > MAX_TRACE_ITEMS:
        raise TraceStorageError(
            "Provenance references too many objects for archive export."
        )
    return sorted(hashes)


def _reference_names(trace: Trace) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {}
    for span in trace.spans:
        for reference in (*span.input_references, *span.output_references):
            names.setdefault(reference.sha256, set()).add(reference.name)
    return names


def _is_potential_source_content(
    metadata: BlobMetadata,
    reference_names: set[str],
) -> bool:
    media = metadata.media_type.lower()
    if media.startswith("text/"):
        return True
    if any(marker in media for marker in _SOURCE_MEDIA_MARKERS):
        return True
    return any(
        any(
            marker in name.lower()
            for marker in ("diff", "patch", "source", "tool.")
        )
        for name in reference_names
    )


def _write_deterministic_zip(
    destination: str | Path,
    files: Mapping[str, bytes],
) -> Path:
    requested = Path(destination).expanduser()
    try:
        parent = requested.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise TraceStorageError(
            "Trace archive destination directory is unavailable."
        ) from exc
    target = parent / requested.name
    if target.exists() or target.is_symlink():
        raise TraceStorageError(
            "Trace archive destination already exists."
        )
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".agentbus-trace-",
            suffix=".tmp",
            dir=parent,
        )
        os.close(descriptor)
        descriptor = -1
        temporary = Path(temporary_name)
        with ZipFile(temporary, mode="w", compression=ZIP_STORED) as archive:
            for name in sorted(files):
                info = ZipInfo(name, date_time=_FIXED_ZIP_TIMESTAMP)
                info.compress_type = ZIP_STORED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(info, files[name])
        with temporary.open("rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    except (OSError, ValueError) as exc:
        raise TraceStorageError("Unable to write trace archive.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return target


def _validate_zip_entry(
    info: ZipInfo,
    *,
    max_uncompressed_bytes: int,
    max_compression_ratio: int,
) -> None:
    try:
        TraceArchiveEntry(
            path=info.filename,
            sha256="0" * 64,
            byte_size=info.file_size,
            media_type="application/octet-stream",
        )
    except Exception as exc:
        raise TraceArchiveError(
            "Trace archive contains an unsafe entry path."
        ) from exc
    if info.is_dir():
        raise TraceArchiveError(
            "Trace archives cannot contain directory entries."
        )
    if info.flag_bits & 0x1:
        raise TraceArchiveError(
            "Encrypted trace archive entries are unsupported."
        )
    if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
        raise TraceArchiveError(
            "Trace archive entry uses unsupported compression."
        )
    if info.file_size > max_uncompressed_bytes:
        raise TraceArchiveError(
            "Trace archive entry exceeds its size bound."
        )
    if info.file_size and info.compress_size == 0:
        raise TraceArchiveError(
            "Trace archive entry has an invalid compression ratio."
        )
    if (
        info.file_size > 1024 * 1024
        and info.file_size / max(info.compress_size, 1)
        > max_compression_ratio
    ):
        raise TraceArchiveError(
            "Trace archive entry exceeds its compression-ratio bound."
        )
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode:
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG} or mode & 0o111:
            raise TraceArchiveError(
                "Trace archive links and executable entries are forbidden."
            )


def _read_zip_entry(
    archive: ZipFile,
    info: ZipInfo,
    *,
    maximum: int,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    with archive.open(info, mode="r") as handle:
        while chunk := handle.read(min(1024 * 1024, maximum - total + 1)):
            total += len(chunk)
            if total > maximum:
                raise TraceArchiveError(
                    f"Trace archive entry '{info.filename}' exceeded its bound."
                )
            chunks.append(chunk)
    payload = b"".join(chunks)
    if len(payload) != info.file_size:
        raise TraceIntegrityError(
            f"Trace archive entry '{info.filename}' was truncated."
        )
    return payload


def _json_mapping(payload: bytes, description: str) -> dict[str, Any]:
    try:
        document = sanitize_document(
            json.loads(payload.decode("utf-8"))
        ).value
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise TraceArchiveError(
            f"Trace archive {description} is not valid bounded JSON."
        ) from exc
    if not isinstance(document, dict):
        raise TraceArchiveError(
            f"Trace archive {description} must be a JSON object."
        )
    return document


__all__ = [
    "MAX_ARCHIVE_ENTRIES",
    "MAX_ARCHIVE_COMPRESSION_RATIO",
    "MAX_ARCHIVE_UNCOMPRESSED_BYTES",
    "TRACE_ARCHIVE_FORMAT",
    "TRACE_ARCHIVE_SCHEMA_VERSION",
    "ImportedTraceArchive",
    "TraceArchiveEntry",
    "TraceArchiveExporter",
    "TraceArchiveImporter",
    "TraceArchiveManifest",
]
