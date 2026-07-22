from __future__ import annotations

import hashlib
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agentbus.tools.filesystem_operations import (
    ContainedFileSystem,
    FileMutationConflict,
    FileMutationOperation,
    FileSizeLimitExceeded,
    PatchConflictError,
)
from agentbus.tools.filesystem_security import (
    FileSystemContainmentError,
    ProtectedFileSystemPath,
)


ATTRIBUTION = {"task_id": "task-1", "invocation_id": "invocation-1"}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_create_is_atomic_attributed_and_immutable(tmp_path: Path) -> None:
    filesystem = ContainedFileSystem(tmp_path)

    record = filesystem.create("src/module.py", "print('safe')\n", **ATTRIBUTION)

    content = (tmp_path / "src" / "module.py").read_bytes()
    assert record.operation == FileMutationOperation.CREATE
    assert record.relative_path == "src/module.py"
    assert record.task_id == "task-1"
    assert record.invocation_id == "invocation-1"
    assert record.before_sha256 is None
    assert record.after_sha256 == digest(content)
    assert record.bytes_before == 0
    assert record.bytes_after == len(content)
    assert record.created is True
    assert record.atomic is True
    with pytest.raises(FrozenInstanceError):
        record.created = False  # type: ignore[misc]


def test_create_refuses_to_replace_an_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("original", encoding="utf-8")

    with pytest.raises(FileExistsError):
        ContainedFileSystem(tmp_path).create(
            "existing.txt",
            "replacement",
            **ATTRIBUTION,
        )

    assert target.read_text(encoding="utf-8") == "original"


def test_write_records_before_and_after_hashes(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    before = digest(b"before")

    record = ContainedFileSystem(tmp_path).write(
        "module.py",
        "after",
        expected_sha256=before,
        **ATTRIBUTION,
    )

    assert record.operation == FileMutationOperation.WRITE
    assert record.before_sha256 == before
    assert record.after_sha256 == digest(b"after")
    assert record.bytes_before == 6
    assert record.bytes_after == 5
    assert record.created is False


def test_write_validates_content_and_expected_hash_types(tmp_path: Path) -> None:
    filesystem = ContainedFileSystem(tmp_path)

    with pytest.raises(TypeError, match="text or bytes"):
        filesystem.write("invalid.bin", 4, **ATTRIBUTION)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        filesystem.write(
            "missing.txt",
            "content",
            expected_sha256="not-a-digest",
            **ATTRIBUTION,
        )


def test_mutation_record_classifies_generated_artifact(tmp_path: Path) -> None:
    record = ContainedFileSystem(tmp_path).create(
        "build/generated.txt",
        "generated",
        **ATTRIBUTION,
    )

    assert record.generated is True


def test_failed_atomic_replace_preserves_original_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "module.py"
    target.write_text("original", encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("injected replacement failure")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected"):
        ContainedFileSystem(tmp_path).write("module.py", "new", **ATTRIBUTION)

    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(".module.py.agentbus-*.tmp")) == []


def test_write_rejects_oversized_input_and_existing_target(tmp_path: Path) -> None:
    filesystem = ContainedFileSystem(tmp_path, maximum_file_bytes=4)

    with pytest.raises(FileSizeLimitExceeded):
        filesystem.write("large.txt", "12345", **ATTRIBUTION)
    (tmp_path / "large.txt").write_text("12345", encoding="utf-8")
    with pytest.raises(FileSizeLimitExceeded):
        filesystem.write("large.txt", "ok", **ATTRIBUTION)


def test_patch_requires_exact_context_and_detects_stale_hash(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("value = 1\nvalue = 1\n", encoding="utf-8")
    filesystem = ContainedFileSystem(tmp_path)

    with pytest.raises(PatchConflictError, match="occurrence"):
        filesystem.patch("module.py", "value = 1", "value = 2", **ATTRIBUTION)
    with pytest.raises(PatchConflictError, match="hash"):
        filesystem.patch(
            "module.py",
            "value = 1",
            "value = 2",
            expected_occurrences=2,
            expected_sha256="0" * 64,
            **ATTRIBUTION,
        )

    record = filesystem.patch(
        "module.py",
        "value = 1",
        "value = 2",
        expected_occurrences=2,
        **ATTRIBUTION,
    )
    assert record.operation == FileMutationOperation.PATCH
    assert target.read_text(encoding="utf-8") == "value = 2\nvalue = 2\n"


def test_patch_rejects_binary_content(tmp_path: Path) -> None:
    (tmp_path / "binary.dat").write_bytes(b"prefix\x00suffix")

    with pytest.raises(ValueError, match="Binary"):
        ContainedFileSystem(tmp_path).patch(
            "binary.dat",
            "prefix",
            "updated",
            **ATTRIBUTION,
        )


def test_rename_and_hash_guarded_delete_preserve_attribution(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    expected = digest(b"payload")
    filesystem = ContainedFileSystem(tmp_path)

    renamed = filesystem.rename(
        "source.txt",
        "nested/destination.txt",
        expected_sha256=expected,
        **ATTRIBUTION,
    )

    assert renamed.operation == FileMutationOperation.RENAME
    assert renamed.source_relative_path == "source.txt"
    assert renamed.relative_path == "nested/destination.txt"
    assert renamed.before_sha256 == renamed.after_sha256 == expected
    assert renamed.atomic is False
    assert source.exists() is False

    with pytest.raises(FileMutationConflict, match="hash"):
        filesystem.delete(
            "nested/destination.txt",
            expected_sha256="0" * 64,
            **ATTRIBUTION,
        )
    deleted = filesystem.delete(
        "nested/destination.txt",
        expected_sha256=expected,
        **ATTRIBUTION,
    )
    assert deleted.operation == FileMutationOperation.DELETE
    assert deleted.before_sha256 == expected
    assert deleted.after_sha256 is None
    assert (tmp_path / "nested" / "destination.txt").exists() is False


def test_mutations_reject_protected_paths_and_link_components(tmp_path: Path) -> None:
    filesystem = ContainedFileSystem(tmp_path)

    with pytest.raises(ProtectedFileSystemPath):
        filesystem.write(".env/child.txt", "secret", **ATTRIBUTION)
    linked = tmp_path / "linked"
    target = tmp_path / "target"
    target.mkdir()
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks require additional privileges")
    with pytest.raises(FileSystemContainmentError, match="symlinks or junctions"):
        filesystem.write("linked/output.txt", "blocked", **ATTRIBUTION)
    assert (target / "output.txt").exists() is False


def test_rename_never_overwrites_destination(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    destination = tmp_path / "destination.txt"
    destination.write_text("destination", encoding="utf-8")

    with pytest.raises(FileExistsError):
        ContainedFileSystem(tmp_path).rename(
            "source.txt",
            "destination.txt",
            **ATTRIBUTION,
        )

    assert destination.read_text(encoding="utf-8") == "destination"
