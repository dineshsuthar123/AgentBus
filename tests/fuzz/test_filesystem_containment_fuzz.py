from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from agentbus.tools.filesystem_operations import ContainedFileSystem
from agentbus.tools.filesystem_security import (
    ContainedPathResolver,
    FileSystemContainmentError,
    FileSystemSecurityError,
    UnsafeFileSystemPath,
    normalize_relative_tool_path,
)


FUZZ_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    derandomize=True,
    database=None,
)
ATTRIBUTION = {"task_id": "task-fuzz", "invocation_id": "inv-fuzz"}
_BACKSLASH = chr(92)
_TOKEN = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=1,
    max_size=24,
)
_RESERVED = st.sampled_from(
    (
        "CON",
        "prn.txt",
        "AuX",
        "nul.data",
        "CLOCK$",
        "com1.log",
        "LPT9",
    )
)


@st.composite
def _ambiguous_paths(draw: st.DrawFn) -> str:
    token = draw(_TOKEN)
    case = draw(
        st.sampled_from(
            (
                "traversal",
                "repeated",
                "mixed_traversal",
                "drive_relative",
                "drive_absolute",
                "unc",
                "device",
                "ads",
                "trailing",
                "reserved",
                "absolute",
                "whitespace",
                "long",
            )
        )
    )
    if case == "traversal":
        return draw(
            st.sampled_from(
                (
                    "..",
                    f"../{token}",
                    f"{token}/../outside",
                    f"{token}/../../outside",
                )
            )
        )
    if case == "repeated":
        return draw(
            st.sampled_from(
                (
                    f"{token}//file.txt",
                    f"{token}{_BACKSLASH * 2}file.txt",
                    f"{token}/",
                )
            )
        )
    if case == "mixed_traversal":
        return f"{token}{_BACKSLASH}..{_BACKSLASH}/outside.txt"
    if case == "drive_relative":
        return f"C:{token}{_BACKSLASH}file.txt"
    if case == "drive_absolute":
        return f"z:{_BACKSLASH}{token}{_BACKSLASH}file.txt"
    if case == "unc":
        return (
            _BACKSLASH * 2
            + token
            + _BACKSLASH
            + "share"
            + _BACKSLASH
            + "file.txt"
        )
    if case == "device":
        return (
            _BACKSLASH * 2
            + "?"
            + _BACKSLASH
            + "C:"
            + _BACKSLASH
            + token
        )
    if case == "ads":
        return f"{token}.txt:{draw(_TOKEN)}"
    if case == "trailing":
        return f"folder/{token}{draw(st.sampled_from((' ', '.')))}"
    if case == "reserved":
        return f"folder/{draw(_RESERVED)}"
    if case == "absolute":
        return draw(
            st.sampled_from(
                (
                    f"/{token}/file.txt",
                    f"{_BACKSLASH}{token}{_BACKSLASH}file.txt",
                )
            )
        )
    if case == "whitespace":
        return draw(
            st.sampled_from((f" {token}.txt", f"{token}.txt ", "\t" + token))
        )
    return token + ("x" * draw(st.integers(min_value=2_049, max_value=2_200)))


@FUZZ_SETTINGS
@given(path=_ambiguous_paths())
def test_ambiguous_paths_cannot_create_files(path: str) -> None:
    with tempfile.TemporaryDirectory(prefix="agentbus-path-fuzz-") as directory:
        root = Path(directory)
        filesystem = ContainedFileSystem(root)

        with pytest.raises(FileSystemSecurityError):
            filesystem.create(path, "must-not-escape", **ATTRIBUTION)

        assert list(root.rglob("*")) == []


@FUZZ_SETTINGS
@given(
    first=_TOKEN,
    second=_TOKEN,
    separator=st.sampled_from(("/", _BACKSLASH)),
)
def test_canonical_and_mixed_paths_publish_only_inside_root(
    first: str,
    second: str,
    separator: str,
) -> None:
    path = f"{first}{separator}{second}.txt"
    with tempfile.TemporaryDirectory(prefix="agentbus-path-fuzz-") as directory:
        root = Path(directory).resolve()
        filesystem = ContainedFileSystem(root)

        record = filesystem.create(path, "contained", **ATTRIBUTION)
        target = (root / first / f"{second}.txt").resolve(strict=True)

        assert record.relative_path == f"{first}/{second}.txt"
        assert target.is_relative_to(root)
        assert target.read_text(encoding="utf-8") == "contained"


@FUZZ_SETTINGS
@given(
    token=_TOKEN,
    unicode_separator=st.sampled_from(("\u2044", "\u2215", "\uff0f")),
)
def test_percent_and_unicode_separators_remain_literal_and_contained(
    token: str,
    unicode_separator: str,
) -> None:
    path = f"encoded-{token}/%2e%2e-%2f{unicode_separator}{token}.txt"
    with tempfile.TemporaryDirectory(prefix="agentbus-path-fuzz-") as directory:
        root = Path(directory).resolve()
        filesystem = ContainedFileSystem(root)

        normalized = normalize_relative_tool_path(path)
        filesystem.create(path, "literal", **ATTRIBUTION)
        target = root.joinpath(*normalized.split("/")).resolve(strict=True)

        assert "%2e%2e" in target.name
        assert unicode_separator in target.name
        assert target.is_relative_to(root)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink chain behavior")
def test_posix_symlink_chain_cannot_escape_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    (root / "second").symlink_to(outside, target_is_directory=True)
    (root / "first").symlink_to(root / "second", target_is_directory=True)

    with pytest.raises(FileSystemContainmentError, match="outside"):
        ContainedPathResolver(root).resolve("first/secret.txt")


def test_parent_link_swap_before_publication_cannot_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    parent = root / "parent"
    root.mkdir()
    outside.mkdir()
    parent.mkdir()
    probe = root / "probe"
    try:
        probe.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory links are unavailable: {exc}")
    probe.unlink()

    filesystem = ContainedFileSystem(root)
    prepare_parents = filesystem._ensure_parent_directories

    def swap_after_preparation(relative_path: str) -> None:
        prepare_parents(relative_path)
        parent.rename(root / "held-parent")
        parent.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(
        filesystem,
        "_ensure_parent_directories",
        swap_after_preparation,
    )

    with pytest.raises(FileSystemContainmentError):
        filesystem.create(
            "parent/private.txt",
            "must-remain-contained",
            **ATTRIBUTION,
        )

    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows path identity behavior")
@pytest.mark.parametrize(
    "path",
    (
        "C:drive-relative.txt",
        "C:/absolute.txt",
        _BACKSLASH * 2 + "server" + _BACKSLASH + "share",
        _BACKSLASH * 2 + "." + _BACKSLASH + "NUL",
        "folder/file.txt:stream",
        "folder/COM9.txt",
    ),
)
def test_windows_special_paths_are_rejected(path: str) -> None:
    with pytest.raises(UnsafeFileSystemPath):
        normalize_relative_tool_path(path)
