from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentbus.intelligence.discovery import (
    DiscoveryLimits,
    RepositoryInventoryScanner,
)
from agentbus.intelligence.errors import UnsafeRepositoryPathError


def test_inventory_respects_nested_gitignore_and_negation(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "*.log\nbuild/\n!keep.log\n",
        encoding="utf-8",
    )
    (tmp_path / "keep.log").write_text("kept", encoding="utf-8")
    (tmp_path / "drop.log").write_text("ignored", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / ".gitignore").write_text(
        "generated.py\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "src" / "generated.py").write_text(
        "value = 2\n",
        encoding="utf-8",
    )
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "output.py").write_text(
        "generated = True\n",
        encoding="utf-8",
    )

    first = RepositoryInventoryScanner(tmp_path).scan()
    second = RepositoryInventoryScanner(tmp_path).scan()
    paths = tuple(item.relative_path for item in first.files)

    assert paths == (
        ".gitignore",
        "keep.log",
        "src/.gitignore",
        "src/app.py",
    )
    assert first.fingerprint == second.fingerprint
    assert first.ignored_count == 3


def test_inventory_excludes_protected_generated_and_vendored_paths(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("SECRET=real\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("SECRET=\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "package.js").write_text(
        "ignored\n",
        encoding="utf-8",
    )
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "library.go").write_text(
        "package vendor\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text(
        "def test_app(): pass\n",
        encoding="utf-8",
    )

    inventory = RepositoryInventoryScanner(tmp_path).scan()
    files = {item.relative_path: item for item in inventory.files}

    assert ".env" not in files
    assert ".env.example" in files
    assert files["tests/test_app.py"].test is True
    assert inventory.generated_roots == ("node_modules",)
    assert inventory.vendored_roots == ("vendor",)
    protected = next(
        item
        for item in inventory.diagnostics
        if item.code == "discovery.protected_path"
    )
    assert protected.relative_path is None
    assert ".env" not in protected.model_dump_json()
    assert "SECRET" not in protected.model_dump_json()


def test_inventory_treats_nested_git_repository_as_separate_root(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "root.py").write_text("def shared(): return 'root'\n", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / ".git").write_text("gitdir: ../metadata\n", encoding="utf-8")
    (nested / "child.py").write_text(
        "def shared(): return 'nested'\n",
        encoding="utf-8",
    )

    parent_inventory = RepositoryInventoryScanner(tmp_path).scan()
    nested_inventory = RepositoryInventoryScanner(nested).scan()

    assert parent_inventory.contains("root.py") is True
    assert parent_inventory.contains("nested/child.py") is False
    boundary = next(
        item
        for item in parent_inventory.diagnostics
        if item.code == "discovery.nested_repository_boundary"
    )
    assert boundary.relative_path == "nested"
    assert nested_inventory.contains("child.py") is True


def test_inventory_rejects_symlink_escape_and_safe_reader_traversal(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.py").write_text("secret = True\n", encoding="utf-8")
    link = tmp_path / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory links is unavailable on this platform.")
    (tmp_path / "safe.toml").write_text("name = 'safe'\n", encoding="utf-8")

    inventory = RepositoryInventoryScanner(tmp_path).scan()

    assert all(
        not item.relative_path.startswith("linked")
        for item in inventory.files
    )
    assert inventory.read_text("safe.toml").splitlines() == ["name = 'safe'"]
    with pytest.raises(UnsafeRepositoryPathError, match="outside"):
        inventory.read_text("../secret.py")
    assert any(
        item.code == "discovery.link_rejected"
        for item in inventory.diagnostics
    )


def test_inventory_stops_at_entry_and_file_size_limits(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"{index}.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "oversized.txt").write_text("x" * 100, encoding="utf-8")
    limits = DiscoveryLimits(
        maximum_entries=4,
        maximum_file_bytes=32,
    )

    inventory = RepositoryInventoryScanner(tmp_path, limits=limits).scan()

    assert inventory.truncated is True
    assert len(inventory.files) <= 4
    assert any(
        item.code == "discovery.entry_limit"
        for item in inventory.diagnostics
    )


def test_oversized_gitignore_is_bounded_and_not_applied(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.py\n" * 10, encoding="utf-8")
    (tmp_path / "ignored.py").write_text("value = 1\n", encoding="utf-8")
    limits = DiscoveryLimits(
        maximum_metadata_bytes=32,
        maximum_file_bytes=1_000,
    )

    inventory = RepositoryInventoryScanner(tmp_path, limits=limits).scan()

    assert inventory.contains("ignored.py") is True
    assert any(
        item.code == "discovery.gitignore_unreadable"
        for item in inventory.diagnostics
    )
