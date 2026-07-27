from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from pydantic import Field, field_validator, model_validator

from agentbus.trace.blobs import BlobMetadata
from agentbus.trace.errors import TraceIntegrityError, TraceStorageError
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
from agentbus.trace.redaction import canonical_json_bytes, sanitize_document
from agentbus.trace.storage import ContentAddressedStore

TRACE_ARCHIVE_SCHEMA_VERSION = 1
TRACE_ARCHIVE_FORMAT = "agentbus.trace-archive"
MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024

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


__all__ = [
    "MAX_ARCHIVE_ENTRIES",
    "MAX_ARCHIVE_UNCOMPRESSED_BYTES",
    "TRACE_ARCHIVE_FORMAT",
    "TRACE_ARCHIVE_SCHEMA_VERSION",
    "TraceArchiveEntry",
    "TraceArchiveExporter",
    "TraceArchiveManifest",
]
