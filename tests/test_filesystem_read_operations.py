from __future__ import annotations

from pathlib import Path

import pytest

from agentbus.tools.filesystem_operations import (
    ContainedFileSystem,
    FileContentKind,
)
from agentbus.tools.filesystem_security import ProtectedFileSystemPath


def test_bounded_text_read_returns_hash_only_for_complete_content(
    tmp_path: Path,
) -> None:
    (tmp_path / "small.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "large.txt").write_text("0123456789", encoding="utf-8")
    filesystem = ContainedFileSystem(tmp_path, maximum_file_bytes=8)

    small = filesystem.read("small.txt")
    large = filesystem.read("large.txt")

    assert small.content == "hello"
    assert small.content_kind == FileContentKind.TEXT
    assert small.sha256 is not None
    assert small.truncated is False
    assert large.content == "01234567"
    assert large.sha256 is None
    assert large.truncated is True
    assert large.size_bytes == 10


def test_binary_read_returns_metadata_without_inline_bytes(tmp_path: Path) -> None:
    (tmp_path / "image.bin").write_bytes(b"header\x00payload")

    result = ContainedFileSystem(tmp_path).read("image.bin")

    assert result.content_kind == FileContentKind.BINARY
    assert result.content is None
    assert result.sha256 is not None
    assert result.bytes_read == 14


def test_utf8_character_split_at_budget_remains_truncated_text(tmp_path: Path) -> None:
    (tmp_path / "unicode.txt").write_text("abc\u00e9z", encoding="utf-8")

    result = ContainedFileSystem(tmp_path, maximum_file_bytes=4).read("unicode.txt")

    assert result.content_kind == FileContentKind.TEXT
    assert result.content == "abc"
    assert result.bytes_read == 3
    assert result.truncated is True


def test_read_redacts_secret_shaped_output(tmp_path: Path) -> None:
    (tmp_path / "diagnostic.txt").write_text(
        "API_KEY=should-not-escape\nready",
        encoding="utf-8",
    )

    result = ContainedFileSystem(tmp_path).read("diagnostic.txt")

    assert result.content is not None
    assert "should-not-escape" not in result.content
    assert "[REDACTED]" in result.content
    assert result.redacted is True


def test_stat_classifies_content_without_returning_file_body(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("print('safe')", encoding="utf-8")

    result = ContainedFileSystem(tmp_path).stat("module.py")

    assert result.is_file is True
    assert result.is_directory is False
    assert result.content_kind == FileContentKind.TEXT
    assert result.size_bytes > 0


def test_recursive_listing_is_deterministic_bounded_and_classified(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "build").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "src" / "b.py").write_text("b", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "build" / "output.txt").write_text("out", encoding="utf-8")
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
    filesystem = ContainedFileSystem(tmp_path, maximum_list_entries=10)

    result = filesystem.list_directory(recursive=True)

    paths = [entry.relative_path for entry in result.entries]
    assert paths == sorted(paths)
    assert ".git" not in paths
    assert ".git" in result.skipped_paths
    generated = next(
        entry for entry in result.entries if entry.relative_path == "build/output.txt"
    )
    assert generated.generated is True

    bounded = filesystem.list_directory(recursive=True, maximum_entries=2)
    assert len(bounded.entries) == 2
    assert bounded.truncated is True
    with pytest.raises(ValueError, match="positive"):
        filesystem.list_directory(maximum_entries=0)


def test_read_and_stat_reject_protected_paths(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    filesystem = ContainedFileSystem(tmp_path)

    with pytest.raises(ProtectedFileSystemPath):
        filesystem.read(".env")
    with pytest.raises(ProtectedFileSystemPath):
        filesystem.stat(".env")
