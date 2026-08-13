from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agentbus.trace.blobs import BlobMetadata, RetentionClass, StoredBlob
from agentbus.trace.errors import (
    TraceIntegrityError,
    TraceNotFoundError,
    TraceObjectTooLargeError,
    TraceSecretRejectedError,
    TraceStorageError,
)
from agentbus.trace.models import TraceInput, TraceOutput, utc_now
from agentbus.trace.redaction import (
    RedactionMetadata,
    canonical_json_bytes,
    contains_secret_material,
    sanitize_document,
    sanitize_text,
)

DEFAULT_MAX_OBJECT_BYTES = 8 * 1024 * 1024
HARD_MAX_OBJECT_BYTES = 64 * 1024 * 1024
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EXECUTABLE_MEDIA_TYPES = {
    "application/x-dosexec",
    "application/x-elf",
    "application/x-executable",
    "application/x-mach-binary",
    "application/x-msdownload",
    "application/x-sharedlib",
}


class ContentAddressedStore:
    """Local, sanitized, immutable blob storage rooted at one safe directory."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
        private_roots: Iterable[str | Path] = (),
    ):
        if max_object_bytes < 1 or max_object_bytes > HARD_MAX_OBJECT_BYTES:
            raise ValueError(
                f"max_object_bytes must be between 1 and {HARD_MAX_OBJECT_BYTES}"
            )
        configured = Path(root).expanduser().absolute()
        _reject_reparse_point(configured)
        try:
            configured.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TraceStorageError(
                f"Unable to create trace object store '{configured}'."
            ) from exc
        _reject_reparse_point(configured)
        self.root = configured.resolve(strict=True)
        self.blob_directory = self.root / "blobs"
        self.metadata_directory = self.root / "metadata"
        self.max_object_bytes = max_object_bytes
        self.private_roots = tuple(private_roots)
        self._lock = threading.RLock()
        self._ensure_directory(self.blob_directory)
        self._ensure_directory(self.metadata_directory)

    def put_json(
        self,
        value: Any,
        *,
        producing_span_id: str,
        media_type: str = "application/json",
        retention_class: RetentionClass = RetentionClass.RUN,
    ) -> BlobMetadata:
        document = sanitize_document(value, private_roots=self.private_roots)
        return self._put_sanitized(
            document.canonical_bytes,
            producing_span_id=producing_span_id,
            media_type=media_type,
            retention_class=retention_class,
            redaction=document.redaction,
        )

    def put_text(
        self,
        value: str,
        *,
        producing_span_id: str,
        media_type: str = "text/plain",
        retention_class: RetentionClass = RetentionClass.RUN,
    ) -> BlobMetadata:
        document = sanitize_text(
            value,
            private_roots=self.private_roots,
            max_chars=self.max_object_bytes,
        )
        return self._put_sanitized(
            document.canonical_bytes,
            producing_span_id=producing_span_id,
            media_type=media_type,
            retention_class=retention_class,
            redaction=document.redaction,
        )

    def put_bytes(
        self,
        value: bytes,
        *,
        producing_span_id: str,
        media_type: str,
        retention_class: RetentionClass = RetentionClass.RUN,
        redaction: RedactionMetadata | None = None,
    ) -> BlobMetadata:
        """Store caller-sanitized UTF-8 bytes after an independent secret scan."""
        if contains_secret_material(value):
            raise TraceSecretRejectedError(
                "Trace storage rejected secret-classified or binary material."
            )
        return self._put_sanitized(
            value,
            producing_span_id=producing_span_id,
            media_type=media_type,
            retention_class=retention_class,
            redaction=redaction
            or RedactionMetadata(
                original_bytes=len(value),
                retained_bytes=len(value),
            ),
        )

    def get(self, sha256: str) -> StoredBlob:
        metadata = self.get_metadata(sha256)
        blob_path, _ = self._object_paths(sha256)
        try:
            data = blob_path.read_bytes()
        except FileNotFoundError as exc:
            raise TraceNotFoundError(
                f"Trace blob '{sha256}' is missing."
            ) from exc
        except OSError as exc:
            raise TraceStorageError(
                f"Unable to read trace blob '{sha256}'."
            ) from exc
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256 or len(data) != metadata.byte_size:
            raise TraceIntegrityError(
                f"Trace blob '{sha256}' failed content integrity validation."
            )
        if len(data) > self.max_object_bytes:
            raise TraceObjectTooLargeError(
                f"Trace blob '{sha256}' exceeds the configured read bound."
            )
        return StoredBlob(metadata=metadata, data=data)

    def get_json(self, sha256: str) -> Any:
        stored = self.get(sha256)
        if not (
            stored.metadata.media_type == "application/json"
            or stored.metadata.media_type.endswith("+json")
        ):
            raise TraceStorageError(
                f"Trace blob '{sha256}' is not a JSON document."
            )
        try:
            return json.loads(stored.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TraceIntegrityError(
                f"Trace blob '{sha256}' is not valid UTF-8 JSON."
            ) from exc

    def get_metadata(self, sha256: str) -> BlobMetadata:
        self._validate_digest(sha256)
        _, metadata_path = self._object_paths(sha256)
        try:
            raw = metadata_path.read_bytes()
        except FileNotFoundError as exc:
            raise TraceNotFoundError(
                f"Trace blob metadata '{sha256}' is missing."
            ) from exc
        except OSError as exc:
            raise TraceStorageError(
                f"Unable to read trace blob metadata '{sha256}'."
            ) from exc
        if len(raw) > 64 * 1024:
            raise TraceIntegrityError(
                f"Trace blob metadata '{sha256}' exceeds its safe bound."
            )
        try:
            metadata = BlobMetadata.model_validate_json(raw)
        except Exception as exc:
            raise TraceIntegrityError(
                f"Trace blob metadata '{sha256}' is invalid."
            ) from exc
        if metadata.sha256 != sha256:
            raise TraceIntegrityError(
                f"Trace blob metadata '{sha256}' has a mismatched hash."
            )
        return metadata

    def reference_input(
        self,
        metadata: BlobMetadata,
        *,
        reference_id: str,
        name: str,
        required_for_replay: bool = True,
    ) -> TraceInput:
        return TraceInput(
            reference_id=reference_id,
            name=name,
            sha256=metadata.sha256,
            media_type=metadata.media_type,
            byte_length=metadata.byte_size,
            redacted=metadata.redaction.applied,
            required_for_replay=required_for_replay,
        )

    def reference_output(
        self,
        metadata: BlobMetadata,
        *,
        reference_id: str,
        name: str,
        replayable: bool = True,
    ) -> TraceOutput:
        return TraceOutput(
            reference_id=reference_id,
            name=name,
            sha256=metadata.sha256,
            media_type=metadata.media_type,
            byte_length=metadata.byte_size,
            redacted=metadata.redaction.applied,
            replayable=replayable,
        )

    def list_metadata(self) -> list[BlobMetadata]:
        self._assert_safe_location(self.metadata_directory)
        results: list[BlobMetadata] = []
        try:
            candidates = sorted(self.metadata_directory.glob("*/*.json"))
        except OSError as exc:
            raise TraceStorageError("Unable to enumerate trace blob metadata.") from exc
        for path in candidates:
            self._assert_safe_location(path)
            digest = path.stem
            if not _DIGEST_PATTERN.fullmatch(digest):
                raise TraceIntegrityError(
                    f"Unexpected trace metadata filename '{path.name}'."
                )
            results.append(self.get_metadata(digest))
        return results

    def verify(self, sha256: str) -> BlobMetadata:
        return self.get(sha256).metadata

    def verify_all(self) -> list[BlobMetadata]:
        metadata = self.list_metadata()
        for item in metadata:
            self.get(item.sha256)
        return metadata

    def delete_unreferenced(
        self,
        sha256: str,
        *,
        referenced_hashes: set[str],
    ) -> int:
        """Delete one confirmed-unreferenced object; never touches external paths."""
        self._validate_digest(sha256)
        if sha256 in referenced_hashes:
            return 0
        blob_path, metadata_path = self._object_paths(sha256)
        reclaimed = 0
        with self._lock:
            if sha256 in referenced_hashes:
                return 0
            try:
                if blob_path.exists():
                    reclaimed = blob_path.stat().st_size
                    blob_path.unlink()
                if metadata_path.exists():
                    metadata_path.unlink()
            except OSError as exc:
                raise TraceStorageError(
                    f"Unable to delete unreferenced trace blob '{sha256}'."
                ) from exc
        return reclaimed

    def _put_sanitized(
        self,
        value: bytes,
        *,
        producing_span_id: str,
        media_type: str,
        retention_class: RetentionClass,
        redaction: RedactionMetadata,
    ) -> BlobMetadata:
        self._validate_payload(value, media_type)
        digest = hashlib.sha256(value).hexdigest()
        blob_path, metadata_path = self._object_paths(digest)
        with self._lock:
            existing = self._load_existing(digest, blob_path, metadata_path)
            if existing is not None:
                merged = _merge_metadata(
                    existing,
                    producing_span_id=producing_span_id,
                    retention_class=retention_class,
                )
                if merged != existing:
                    self._atomic_write(
                        metadata_path,
                        canonical_json_bytes(merged.model_dump(mode="json")),
                    )
                return merged
            metadata = BlobMetadata(
                sha256=digest,
                media_type=media_type,
                byte_size=len(value),
                redaction=redaction,
                created_at=utc_now(),
                producing_span_ids=[producing_span_id],
                retention_classes=[retention_class],
            )
            self._atomic_write(blob_path, value)
            try:
                self._atomic_write(
                    metadata_path,
                    canonical_json_bytes(metadata.model_dump(mode="json")),
                )
            except Exception:
                self._discard_uncommitted_blob(blob_path, metadata_path)
                raise
            return metadata

    def _discard_uncommitted_blob(
        self,
        blob_path: Path,
        metadata_path: Path,
    ) -> None:
        try:
            self._assert_safe_location(blob_path)
            self._assert_safe_location(metadata_path)
            if not metadata_path.exists():
                blob_path.unlink(missing_ok=True)
        except (OSError, TraceStorageError):
            pass

    def _load_existing(
        self,
        digest: str,
        blob_path: Path,
        metadata_path: Path,
    ) -> BlobMetadata | None:
        blob_exists = blob_path.exists()
        metadata_exists = metadata_path.exists()
        if not blob_exists and not metadata_exists:
            return None
        if blob_exists != metadata_exists:
            raise TraceIntegrityError(
                f"Trace object '{digest}' has incomplete persisted state."
            )
        existing = self.get(digest)
        return existing.metadata

    def _validate_payload(self, value: bytes, media_type: str) -> None:
        if not media_type or len(media_type) > 200:
            raise TraceStorageError("Trace object media type is invalid.")
        normalized_media_type = media_type.split(";", 1)[0].strip().lower()
        if normalized_media_type in _EXECUTABLE_MEDIA_TYPES:
            raise TraceStorageError("Executable trace objects are not supported.")
        if len(value) > self.max_object_bytes:
            raise TraceObjectTooLargeError(
                f"Trace object is {len(value)} bytes; limit is "
                f"{self.max_object_bytes} bytes."
            )
        try:
            value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TraceStorageError(
                "Trace objects must be sanitized UTF-8 documents."
            ) from exc
        if contains_secret_material(value):
            raise TraceSecretRejectedError(
                "Trace storage rejected unredacted secret-classified material."
            )

    def _object_paths(self, sha256: str) -> tuple[Path, Path]:
        self._validate_digest(sha256)
        self._assert_safe_location(self.blob_directory)
        self._assert_safe_location(self.metadata_directory)
        blob_parent = self.blob_directory / sha256[:2]
        metadata_parent = self.metadata_directory / sha256[:2]
        self._ensure_directory(blob_parent)
        self._ensure_directory(metadata_parent)
        blob_path = blob_parent / sha256
        metadata_path = metadata_parent / f"{sha256}.json"
        self._assert_safe_location(blob_path)
        self._assert_safe_location(metadata_path)
        return blob_path, metadata_path

    def _ensure_directory(self, path: Path) -> None:
        self._assert_safe_location(path.parent)
        try:
            path.mkdir(parents=False, exist_ok=True)
        except OSError as exc:
            raise TraceStorageError(
                f"Unable to create trace storage directory '{path.name}'."
            ) from exc
        self._assert_safe_location(path)

    def _atomic_write(self, destination: Path, value: bytes) -> None:
        self._assert_safe_location(destination)
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".agentbus-tmp-",
                dir=destination.parent,
            )
            temporary = Path(temporary_name)
            self._assert_safe_location(temporary)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            self._assert_safe_location(destination)
            os.replace(temporary, destination)
            temporary = None
        except OSError as exc:
            raise TraceStorageError(
                f"Unable to atomically write trace object '{destination.name}'."
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _assert_safe_location(self, path: Path) -> None:
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise TraceStorageError(
                "Trace object path escapes the configured storage root."
            ) from exc
        current = self.root
        relative_parts = path.absolute().relative_to(self.root).parts
        for part in relative_parts:
            current = current / part
            _reject_reparse_point(current)

    @staticmethod
    def _validate_digest(sha256: str) -> None:
        if not _DIGEST_PATTERN.fullmatch(sha256):
            raise TraceStorageError("Trace object hash must be lowercase SHA-256.")


def _merge_metadata(
    metadata: BlobMetadata,
    *,
    producing_span_id: str,
    retention_class: RetentionClass,
) -> BlobMetadata:
    spans = sorted({*metadata.producing_span_ids, producing_span_id})
    retentions = sorted(
        {*metadata.retention_classes, retention_class},
        key=lambda item: item.value,
    )
    return BlobMetadata.model_validate(
        metadata.model_copy(
            update={
                "producing_span_ids": spans,
                "retention_classes": retentions,
            }
        ).model_dump()
    )


def _reject_reparse_point(path: Path) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise TraceStorageError(
            f"Unable to inspect trace storage path '{path}'."
        ) from exc
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if path.is_symlink() or bool(attributes & reparse_flag):
        raise TraceStorageError(
            "Trace storage refuses symbolic links, junctions, and reparse points."
        )


__all__ = [
    "DEFAULT_MAX_OBJECT_BYTES",
    "HARD_MAX_OBJECT_BYTES",
    "ContentAddressedStore",
]
