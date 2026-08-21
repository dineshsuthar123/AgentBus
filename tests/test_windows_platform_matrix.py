from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from agentbus.intelligence.discovery.scanner import RepositoryInventoryScanner
from agentbus.sandbox import (
    ControlledProcessSupervisor,
    ExecutableCatalog,
    ExecutableValidationError,
    WorkingDirectoryValidationError,
    validate_working_directory,
)
from agentbus.sandbox.platform import windows_system_command_processor
from agentbus.tools.filesystem_operations import ContainedFileSystem
from agentbus.tools.filesystem_security import (
    ContainedPathResolver,
    FileSystemContainmentError,
    FileSystemSecurityError,
    UnsafeFileSystemPath,
    normalize_relative_tool_path,
)


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows platform matrix")
_ATTRIBUTION = {"task_id": "windows-matrix", "invocation_id": "invocation-1"}


def test_drive_root_containment_accepts_only_canonical_descendants(
    tmp_path: Path,
) -> None:
    drive_root = Path(tmp_path.anchor)

    assert drive_root.is_dir()
    assert validate_working_directory(drive_root, tmp_path) == tmp_path.resolve()
    with pytest.raises(UnsafeFileSystemPath, match="Absolute"):
        normalize_relative_tool_path(str(tmp_path / "absolute.txt"))


def test_unc_roots_are_rejected_before_canonicalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_calls: list[Path] = []

    def unexpected_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        resolve_calls.append(path)
        raise AssertionError("UNC root reached filesystem canonicalization")

    monkeypatch.setattr(Path, "resolve", unexpected_resolve)
    unc_root = r"\\agentbus.invalid\untrusted-share"

    with pytest.raises(FileSystemSecurityError, match="UNC"):
        ContainedPathResolver(unc_root)
    with pytest.raises(WorkingDirectoryValidationError, match="UNC"):
        validate_working_directory(unc_root)

    assert resolve_calls == []


def test_long_path_round_trips_through_filesystem_and_inventory(
    tmp_path: Path,
) -> None:
    segments: list[str] = []
    while len(str(tmp_path.joinpath(*segments, "payload.txt"))) <= 280:
        segments.append(f"segment-{len(segments):02d}-" + ("x" * 32))
    relative_path = PurePosixPath(*segments, "payload.txt").as_posix()
    target = tmp_path.joinpath(*segments, "payload.txt")
    probe = target.with_name("probe.txt")

    try:
        probe.parent.mkdir(parents=True)
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        pytest.skip(f"Windows long paths are unavailable: {type(exc).__name__}")

    filesystem = ContainedFileSystem(tmp_path)
    record = filesystem.create(relative_path, "long-path", **_ATTRIBUTION)
    inventory = RepositoryInventoryScanner(tmp_path).scan()

    assert len(str(target)) > 260
    assert record.relative_path == relative_path
    assert filesystem.read(relative_path).content == "long-path"
    assert inventory.contains(relative_path)


@pytest.mark.parametrize(
    "path",
    [
        "CON",
        "nul.txt",
        "COM9.log",
        "LPT1",
        "CONIN$",
        "CONOUT$.log",
        "COM\u00b9.txt",
        "LPT\u00b2",
        "COM\u00b3.log",
        "payload.txt:metadata",
        r"\\server\share\payload.txt",
        r"\\?\C:\workspace\payload.txt",
    ],
)
def test_unsafe_windows_names_never_reach_the_filesystem(path: str) -> None:
    with pytest.raises(UnsafeFileSystemPath):
        normalize_relative_tool_path(path)


def test_true_junction_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    junction = root / "junction"
    root.mkdir()
    outside.mkdir()
    (outside / "payload.txt").write_text("outside", encoding="utf-8")
    _create_junction(junction, outside)

    try:
        with pytest.raises(FileSystemContainmentError, match="outside"):
            ContainedPathResolver(root).resolve("junction/payload.txt")
    finally:
        junction.rmdir()


def test_junction_rejection_has_a_legacy_python_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    target = root / "target"
    junction = root / "junction"
    target.mkdir(parents=True)
    (target / "payload.txt").write_text("inside", encoding="utf-8")
    _create_junction(junction, target)
    resolver = ContainedPathResolver(root)
    monkeypatch.setattr(Path, "is_junction", lambda _path: False, raising=False)

    try:
        with pytest.raises(FileSystemContainmentError, match="symlinks or junctions"):
            resolver.resolve("junction/payload.txt", reject_any_link=True)
    finally:
        junction.rmdir()


def test_case_insensitive_alias_and_path_collisions_are_refused(
    tmp_path: Path,
) -> None:
    with pytest.raises(ExecutableValidationError, match="Duplicate"):
        ExecutableCatalog({"Python": sys.executable, "python.exe": sys.executable})

    source = tmp_path / "Report.TXT"
    source.write_text("preserve", encoding="utf-8")
    if not (tmp_path / "report.txt").exists():
        pytest.skip("Temporary directory uses case-sensitive Windows semantics")

    with pytest.raises(FileExistsError, match="already exists"):
        ContainedFileSystem(tmp_path).rename(
            "Report.TXT",
            "report.txt",
            **_ATTRIBUTION,
        )

    assert source.read_text(encoding="utf-8") == "preserve"
    assert len(tuple(tmp_path.iterdir())) == 1


def test_python_launcher_executes_the_pinned_interpreter(tmp_path: Path) -> None:
    supervisor = ControlledProcessSupervisor(
        tmp_path,
        catalog=ExecutableCatalog.standard(("python",)),
    )
    result = supervisor.run(
        "python.exe",
        (
            "-c",
            "import json, sys; print(json.dumps({'executable': sys.executable}))",
        ),
    )
    child = Path(json.loads(result.stdout)["executable"]).resolve()
    expected = Path(sys.executable).resolve()
    metadata = result.safe_diagnostic_metadata

    assert result.passed is True
    assert os.path.normcase(str(child)) == os.path.normcase(str(expected))
    assert result.executable.path == expected
    assert metadata["executable"]["path"] == str(expected)
    assert metadata["launch_backend"] == "direct"
    assert metadata["shell"] is False


def _create_junction(junction: Path, target: Path) -> None:
    command_processor = windows_system_command_processor()
    completed = subprocess.run(
        [
            str(command_processor),
            "/d",
            "/c",
            "mklink",
            "/J",
            str(junction),
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")
    assert junction.is_dir()
