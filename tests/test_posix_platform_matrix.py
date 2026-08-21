from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentbus.control import registry as control_registry
from agentbus.control.errors import ControlPlaneConflictError
from agentbus.control.models import DaemonRegistryEntry
from agentbus.control.registry import (
    DaemonRegistry,
    executable_identity,
    process_matches,
    process_start_identity,
    terminate_registered_daemon,
)
from agentbus.product import quickstart
from agentbus.sandbox import (
    ControlledProcessSupervisor,
    ExecutableCatalog,
    ExecutableValidationError,
)
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


def test_executable_permission_change_invalidates_pinned_identity(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "runner"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    catalog = ExecutableCatalog({"runner": executable})

    executable.chmod(0o600)

    with pytest.raises(ExecutableValidationError, match="identity changed"):
        catalog.resolve("runner")


def test_daemon_owner_mismatch_prevents_posix_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "registry.json"
    registry = DaemonRegistry(registry_path)
    entry = _current_daemon_entry(registry_path)
    registry.register(entry)
    current_uid = os.geteuid()
    real_kill = os.kill
    signals: list[tuple[int, int]] = []

    def observe_signal(pid: int, signal_number: int) -> None:
        signals.append((pid, signal_number))
        if signal_number == 0:
            real_kill(pid, signal_number)

    monkeypatch.setattr(control_registry.os, "geteuid", lambda: current_uid + 1)
    monkeypatch.setattr(control_registry.os, "kill", observe_signal)

    assert process_matches(entry) is False
    with pytest.raises(ControlPlaneConflictError, match="no process was stopped"):
        terminate_registered_daemon(registry, entry.daemon_id)

    assert signals
    assert all(item == (entry.pid, 0) for item in signals)
    assert registry.list() == []


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


def test_owned_cleanup_removes_read_only_directory_and_preserves_sibling(
    tmp_path: Path,
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root does not exercise POSIX owner permission denial")
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("preserve", encoding="utf-8")
    owner_token = "posix-readonly-owner"
    container = quickstart._create_repository(tmp_path, owner_token)
    readonly = container / "readonly"
    readonly.mkdir()
    (readonly / "payload.txt").write_text("owned", encoding="utf-8")
    readonly.chmod(0o500)

    try:
        quickstart._remove_owned_container(container, tmp_path, owner_token)
        assert container.exists() is False
        assert unrelated.read_text(encoding="utf-8") == "preserve"
    finally:
        if readonly.exists():
            readonly.chmod(0o700)
        if container.exists():
            shutil.rmtree(container)


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


def _current_daemon_entry(registry_path: Path) -> DaemonRegistryEntry:
    now = datetime.now(timezone.utc)
    return DaemonRegistryEntry(
        daemon_id="posix-owner-probe",
        pid=os.getpid(),
        executable=executable_identity(),
        process_start_identity=process_start_identity(),
        host="127.0.0.1",
        port=43123,
        agentbus_version="0.7.0",
        started_at=now,
        heartbeat_at=now,
        state_database=str(registry_path.parent / "state.db"),
        registry_path=str(registry_path),
    )


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
