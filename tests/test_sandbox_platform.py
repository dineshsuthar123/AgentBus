from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agentbus.sandbox import (
    ExecutableCatalog,
    ExecutableValidationError,
    WorkingDirectoryValidationError,
    validate_working_directory,
)


def test_standard_catalog_pins_python_and_pytest_to_current_interpreter() -> None:
    catalog = ExecutableCatalog.standard(("python", "pytest"))

    python = catalog.resolve("python")
    pytest_tool = catalog.resolve("pytest")

    assert python.path == Path(sys.executable).resolve()
    assert python.argument_prefix == ()
    assert pytest_tool.path == Path(sys.executable).resolve()
    assert pytest_tool.argument_prefix == ("-m", "pytest")
    assert len(python.sha256) == 64


def test_catalog_ignores_path_changes_and_rejects_unregistered_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = ExecutableCatalog.standard(("python",))
    hijack = tmp_path / ("python.exe" if os.name == "nt" else "python")
    hijack.write_text("not an executable", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))

    assert catalog.resolve("python").path == Path(sys.executable).resolve()
    with pytest.raises(ExecutableValidationError, match="not explicitly allowlisted"):
        catalog.resolve("node")
    with pytest.raises(ExecutableValidationError, match="not explicitly allowlisted"):
        catalog.resolve(hijack)


def test_catalog_rejects_executable_replacement(tmp_path: Path) -> None:
    executable = tmp_path / "runner"
    executable.write_bytes(b"version-one")
    catalog = ExecutableCatalog({"runner": executable})

    executable.write_bytes(b"version-two")

    with pytest.raises(ExecutableValidationError, match="identity changed"):
        catalog.resolve("runner")


def test_working_directory_must_resolve_inside_assigned_worktree(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    nested = worktree / "src"
    outside = tmp_path / "outside"
    nested.mkdir(parents=True)
    outside.mkdir()

    assert validate_working_directory(worktree) == worktree.resolve()
    assert validate_working_directory(worktree, "src") == nested.resolve()
    with pytest.raises(WorkingDirectoryValidationError, match="inside"):
        validate_working_directory(worktree, outside)
    with pytest.raises(WorkingDirectoryValidationError, match="inside"):
        validate_working_directory(worktree, "../outside")


def test_working_directory_rejects_unc_device_and_ads_syntax(tmp_path: Path) -> None:
    with pytest.raises(WorkingDirectoryValidationError, match="UNC"):
        validate_working_directory(tmp_path, r"\\server\share")
    with pytest.raises(WorkingDirectoryValidationError, match="UNC"):
        validate_working_directory(tmp_path, r"\\?\C:\workspace")
    with pytest.raises(WorkingDirectoryValidationError, match="alternate data"):
        validate_working_directory(tmp_path, "src:metadata")
