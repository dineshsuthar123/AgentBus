from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentbus.product import quickstart
from agentbus.sandbox import ControlledProcessSupervisor, ExecutableCatalog
from agentbus.tools.filesystem_operations import ContainedFileSystem
from agentbus.tools.filesystem_security import (
    ContainedPathResolver,
    FileSystemContainmentError,
)


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX platform matrix")
_ATTRIBUTION = {"task_id": "posix-matrix", "invocation_id": "invocation-1"}


def test_symlink_chain_cannot_escape_contained_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "payload.txt").write_text("outside", encoding="utf-8")
    (root / "second").symlink_to(outside, target_is_directory=True)
    (root / "first").symlink_to(root / "second", target_is_directory=True)

    with pytest.raises(FileSystemContainmentError, match="outside"):
        ContainedPathResolver(root).resolve("first/payload.txt")


def test_deleted_current_working_directory_has_a_bounded_diagnostic(
    tmp_path: Path,
) -> None:
    working_directory = tmp_path / "deleted-cwd"
    ready = tmp_path / "ready"
    proceed = tmp_path / "proceed"
    working_directory.mkdir()
    source_root = Path(__file__).resolve().parents[1]
    script = "\n".join(
        (
            "import json, sys, time",
            "from pathlib import Path",
            "sys.path.insert(0, sys.argv[3])",
            "from agentbus.sandbox import (",
            "    WorkingDirectoryValidationError, validate_working_directory",
            ")",
            "ready, proceed = Path(sys.argv[1]), Path(sys.argv[2])",
            "ready.write_text('ready', encoding='utf-8')",
            "while not proceed.exists():",
            "    time.sleep(0.01)",
            "try:",
            "    validate_working_directory('.')",
            "except WorkingDirectoryValidationError as exc:",
            "    print(json.dumps({'type': type(exc).__name__, 'message': str(exc)}))",
            "else:",
            "    raise SystemExit('deleted working directory was accepted')",
        )
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(ready), str(proceed), str(source_root)],
        cwd=working_directory,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )

    try:
        _wait_for_path(ready, process)
        working_directory.rmdir()
        proceed.write_text("proceed", encoding="utf-8")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    payload = json.loads(stdout)
    assert process.returncode == 0, stderr
    assert payload["type"] == "WorkingDirectoryValidationError"
    assert payload["message"] == (
        "Assigned worktree does not exist or cannot be resolved."
    )
    assert len(stdout) < 300


def test_executable_symlink_is_pinned_to_its_canonical_interpreter(
    tmp_path: Path,
) -> None:
    expected = Path(sys.executable).resolve()
    launcher = tmp_path / "python-link"
    launcher.symlink_to(expected)
    catalog = ExecutableCatalog({"python-link": launcher})
    supervisor = ControlledProcessSupervisor(tmp_path, catalog=catalog)

    result = supervisor.run(
        "python-link",
        ("-c", "import sys; print(sys.executable)"),
    )

    assert result.passed is True
    assert result.executable.path == expected
    assert Path(result.stdout.strip()).resolve() == expected
    assert result.safe_diagnostic_metadata["launch_backend"] == "direct"
    assert result.safe_diagnostic_metadata["shell"] is False


def test_read_only_directory_refuses_mutation_without_partial_files(
    tmp_path: Path,
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root does not exercise POSIX owner permission denial")
    root = tmp_path / "root"
    readonly = root / "readonly"
    readonly.mkdir(parents=True)
    readonly.chmod(0o500)

    try:
        with pytest.raises(PermissionError):
            ContainedFileSystem(root).create(
                "readonly/payload.txt",
                "must-not-exist",
                **_ATTRIBUTION,
            )
        assert list(readonly.iterdir()) == []
    finally:
        readonly.chmod(0o700)


def test_symlinked_temporary_parent_is_canonicalized_before_owned_cleanup(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real temp parent"
    alias = tmp_path / "temp-alias"
    unrelated = real_parent / "unrelated.txt"
    real_parent.mkdir()
    unrelated.write_text("preserve", encoding="utf-8")
    alias.symlink_to(real_parent, target_is_directory=True)
    parent = quickstart._temporary_parent(alias)
    owner_token = "posix-matrix-owner"
    container = quickstart._create_repository(parent, owner_token)

    assert parent == real_parent.resolve()
    assert container.parent == real_parent.resolve()
    quickstart._remove_owned_container(container, parent, owner_token)

    assert container.exists() is False
    assert alias.is_symlink()
    assert unrelated.read_text(encoding="utf-8") == "preserve"


def _wait_for_path(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(
                "deleted-CWD helper exited before synchronization: "
                f"stdout={stdout!r}, stderr={stderr!r}"
            )
        time.sleep(0.01)
    pytest.fail("deleted-CWD helper did not become ready")
