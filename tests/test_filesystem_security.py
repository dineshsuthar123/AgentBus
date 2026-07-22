from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentbus.tools.filesystem_security import (
    ContainedPathResolver,
    FileSystemContainmentError,
    FileSystemSecurityError,
    ProtectedFileSystemPath,
    UnsafeFileSystemPath,
    normalize_relative_tool_path,
)


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "nested/../../outside.txt",
        "/etc/passwd",
        r"C:\Windows\system.ini",
        r"\\server\share\file.txt",
        r"\\?\C:\workspace\file.txt",
        "file.txt:secret",
        "CON",
        "nul.txt",
        "folder//file.txt",
        " file.txt",
        "file.txt ",
        "file.txt.",
    ],
)
def test_path_normalization_rejects_ambiguous_and_special_paths(path: str) -> None:
    with pytest.raises(UnsafeFileSystemPath):
        normalize_relative_tool_path(path)


def test_resolver_accepts_canonical_paths_and_classifies_generated_files(
    tmp_path: Path,
) -> None:
    resolver = ContainedPathResolver(tmp_path)

    source = resolver.resolve("./src/module.py")
    generated = resolver.resolve("build/output.txt")
    environment_example = resolver.resolve(".env.example")

    assert source.relative_path == "src/module.py"
    assert source.path == (tmp_path / "src" / "module.py").resolve()
    assert source.classification.generated is False
    assert generated.classification.generated is True
    assert generated.classification.generated_reason is not None
    assert environment_example.classification.protected is False


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "private.pem",
        ".git/config",
        ".agentbus/state.db",
        ".ssh/id_ed25519",
        ".aws/credentials",
        ".docker/config.json",
        ".kube/config",
        ".env.production",
        "custom-state/durable.sqlite3",
        "credentials.json",
        "daemons.json",
    ],
)
def test_resolver_rejects_secret_and_control_plane_paths(
    tmp_path: Path,
    path: str,
) -> None:
    resolver = ContainedPathResolver(tmp_path)

    with pytest.raises(ProtectedFileSystemPath):
        resolver.resolve(path)


def test_resolver_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    resolver = ContainedPathResolver(root)

    with pytest.raises(FileSystemContainmentError, match="outside"):
        resolver.resolve("linked/secret.txt")


def test_resolver_allows_link_that_stays_inside_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    resolved = ContainedPathResolver(tmp_path).resolve("linked/file.txt")

    assert resolved.path == (target / "file.txt").resolve()


def test_resolver_detects_assigned_root_replacement(tmp_path: Path) -> None:
    root = tmp_path / "root"
    moved = tmp_path / "moved-root"
    root.mkdir()
    resolver = ContainedPathResolver(root)
    root.rename(moved)
    root.mkdir()

    with pytest.raises(FileSystemSecurityError, match="identity changed"):
        resolver.resolve("file.txt")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
def test_resolver_rejects_junction_escape_when_available(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    junction = root / "junction"
    try:
        junction.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"junction-compatible link creation unavailable: {exc}")

    with pytest.raises(FileSystemContainmentError, match="outside"):
        ContainedPathResolver(root).resolve("junction/file.txt")
