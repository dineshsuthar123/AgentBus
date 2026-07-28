import os
from pathlib import Path

import pytest

from agentbus.trace import (
    ContentAddressedStore,
    RetentionClass,
    TraceIntegrityError,
    TraceObjectTooLargeError,
    TraceSecretRejectedError,
    TraceStorageError,
)


def test_content_addressed_store_deduplicates_and_merges_references(
    tmp_path: Path,
) -> None:
    store = ContentAddressedStore(tmp_path / "trace-store")

    first = store.put_json(
        {"answer": 42},
        producing_span_id="span-1",
        retention_class=RetentionClass.RUN,
    )
    second = store.put_json(
        {"answer": 42},
        producing_span_id="span-2",
        retention_class=RetentionClass.FIXTURE,
    )

    assert first.sha256 == second.sha256
    assert second.producing_span_ids == ["span-1", "span-2"]
    assert set(second.retention_classes) == {
        RetentionClass.RUN,
        RetentionClass.FIXTURE,
    }
    assert store.get_json(first.sha256) == {"answer": 42}
    assert len(store.list_metadata()) == 1


def test_store_sanitizes_json_before_hashing(tmp_path: Path) -> None:
    store = ContentAddressedStore(
        tmp_path / "trace-store",
        private_roots=[tmp_path],
    )

    metadata = store.put_json(
        {
            "api_key": "real-secret",
            "path": str(tmp_path / "workspace" / "file.py"),
        },
        producing_span_id="span-1",
    )
    stored = store.get(metadata.sha256)

    assert b"real-secret" not in stored.data
    assert str(tmp_path).encode() not in stored.data
    assert metadata.redaction.applied is True


def test_store_rejects_unredacted_secrets_binary_and_executables(
    tmp_path: Path,
) -> None:
    store = ContentAddressedStore(tmp_path / "trace-store")

    with pytest.raises(TraceSecretRejectedError):
        store.put_bytes(
            b"authorization=real-secret",
            producing_span_id="span-1",
            media_type="text/plain",
        )
    with pytest.raises(TraceSecretRejectedError):
        store.put_bytes(
            b"\x00\xff",
            producing_span_id="span-1",
            media_type="application/octet-stream",
        )
    with pytest.raises(TraceStorageError, match="Executable"):
        store.put_bytes(
            b"safe text",
            producing_span_id="span-1",
            media_type="application/x-executable",
        )


def test_store_enforces_write_and_read_size_bounds(tmp_path: Path) -> None:
    store = ContentAddressedStore(tmp_path / "trace-store", max_object_bytes=16)

    with pytest.raises(TraceObjectTooLargeError, match="limit is 16"):
        store.put_text("x" * 100, producing_span_id="span-1")


def test_store_detects_blob_and_metadata_tampering(tmp_path: Path) -> None:
    root = tmp_path / "trace-store"
    store = ContentAddressedStore(root)
    metadata = store.put_text("safe", producing_span_id="span-1")
    blob_path = root / "blobs" / metadata.sha256[:2] / metadata.sha256

    blob_path.write_text("tampered", encoding="utf-8")
    with pytest.raises(TraceIntegrityError, match="content integrity"):
        store.get(metadata.sha256)

    blob_path.write_text("safe", encoding="utf-8")
    metadata_path = (
        root / "metadata" / metadata.sha256[:2] / f"{metadata.sha256}.json"
    )
    metadata_path.write_text("{}", encoding="utf-8")
    with pytest.raises(TraceIntegrityError, match="metadata"):
        store.get_metadata(metadata.sha256)


def test_store_rejects_hash_traversal(tmp_path: Path) -> None:
    store = ContentAddressedStore(tmp_path / "trace-store")

    with pytest.raises(TraceStorageError, match="lowercase SHA-256"):
        store.get("../../outside")


def test_store_rejects_symlink_or_junction_escape(tmp_path: Path) -> None:
    root = tmp_path / "trace-store"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = ContentAddressedStore(root)
    store.blob_directory.rmdir()
    try:
        os.symlink(outside, store.blob_directory, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory links is unavailable on this platform.")

    with pytest.raises(TraceStorageError, match="symbolic links|escapes"):
        store.put_text("safe", producing_span_id="span-1")
    assert list(outside.iterdir()) == []


def test_atomic_writes_leave_no_temporary_objects(tmp_path: Path) -> None:
    root = tmp_path / "trace-store"
    store = ContentAddressedStore(root)
    store.put_text("safe", producing_span_id="span-1")

    temporary = list(root.rglob(".agentbus-tmp-*"))
    assert temporary == []
