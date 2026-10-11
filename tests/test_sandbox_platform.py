from __future__ import annotations

import os
import sys
import venv
from pathlib import Path

import pytest

from agentbus.sandbox import (
    ExecutableCatalog,
    ExecutableValidationError,
    WorkingDirectoryValidationError,
    validate_working_directory,
)
from agentbus.sandbox.process import ControlledProcessSupervisor


def test_standard_catalog_pins_python_and_pytest_to_current_interpreter() -> None:
    catalog = ExecutableCatalog.standard(("python", "pytest"))

    python = catalog.resolve("python")
    pytest_tool = catalog.resolve("pytest")

    assert python.path == Path(sys.executable).absolute()
    assert python.canonical_path == Path(sys.executable).resolve()
    assert python.argument_prefix == ()
    assert pytest_tool.path == Path(sys.executable).absolute()
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

    assert catalog.resolve("python").path == Path(sys.executable).absolute()
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


def test_catalog_pins_virtualenv_metadata_including_absence(tmp_path: Path) -> None:
    binary = tmp_path / "bin" / "python"
    binary.parent.mkdir()
    binary.write_bytes(b"interpreter")
    catalog = ExecutableCatalog({"python": binary})
    config = tmp_path / "pyvenv.cfg"
    config.write_text("home = /trusted\n", encoding="utf-8")
    with pytest.raises(ExecutableValidationError, match="identity changed"):
        catalog.resolve("python")
    catalog = ExecutableCatalog({"python": binary})
    config.write_text("home = /changed\n", encoding="utf-8")
    with pytest.raises(ExecutableValidationError, match="identity changed"):
        catalog.resolve("python")


@pytest.mark.parametrize("symlinks", [False, True])
def test_managed_virtualenv_preserves_pytest_dependencies(tmp_path: Path, symlinks: bool) -> None:
    if symlinks and os.name == "nt":
        pytest.skip("Windows symlink creation requires optional host privilege")
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=symlinks).create(environment)
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    # Supply the already-installed test dependencies offline, exclusively to this venv.
    (site / "test-dependencies.pth").write_text(str(Path(pytest.__file__).parent.parent) + "\n", encoding="utf-8")
    (tmp_path / "test_environment.py").write_text(
        "import os, sys\ndef test_environment():\n"
        f"    assert sys.prefix == {str(environment)!r}\n"
        "    assert os.environ['PYTHONNOUSERSITE'] == '1'\n"
        "    assert 'AZURE_OPENAI_API_KEY' not in os.environ\n", encoding="utf-8",
    )
    supervisor = ControlledProcessSupervisor(
        tmp_path, catalog=ExecutableCatalog({"python": executable}),
        source_environment={**os.environ, "AZURE_OPENAI_API_KEY": "test-only-secret"},
    )
    result = supervisor.run("python", ["-m", "pytest", "-q", "test_environment.py"], timeout_seconds=30)
    assert result.passed, result.stderr + result.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink launch semantics")
def test_catalog_preserves_launch_path_and_detects_link_retargeting(tmp_path: Path) -> None:
    binary = tmp_path / "python"
    intermediate = tmp_path / "interpreter"
    intermediate.symlink_to(sys.executable)
    binary.symlink_to(intermediate)
    catalog = ExecutableCatalog({"python": binary})
    assert catalog.resolve("python").command([]) == [str(binary)]
    intermediate.unlink()
    intermediate.symlink_to(Path(sys.executable).resolve())
    with pytest.raises(ExecutableValidationError, match="identity changed"):
        catalog.resolve("python")


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
