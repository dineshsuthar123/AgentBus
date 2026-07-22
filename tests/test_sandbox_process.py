from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from agentbus.execution.cancellation import CancellationToken
from agentbus.sandbox import (
    ControlledProcessSupervisor,
    ExecutableCatalog,
    ProcessSupervisionError,
)
from agentbus.tools.protocol import ToolOutputChunk, ToolResourceBudget


def _supervisor(
    worktree: Path,
    *,
    source_environment: dict[str, str] | None = None,
) -> ControlledProcessSupervisor:
    return ControlledProcessSupervisor(
        worktree,
        catalog=ExecutableCatalog.standard(("python",)),
        source_environment=source_environment,
        poll_interval_seconds=0.01,
        termination_grace_seconds=0.5,
    )


def test_supervisor_executes_arguments_without_shell_parsing(tmp_path: Path) -> None:
    result = _supervisor(tmp_path).run(
        "python",
        ("-c", "print('shell data: && | ; > <')"),
    )

    assert result.passed is True
    assert result.stdout.strip() == "shell data: && | ; > <"
    assert result.stderr == ""
    assert result.safe_diagnostic_metadata["shell"] is False
    assert result.process_tree is not None
    assert result.resource_usage.limits["wall_clock_seconds"].enforced is True


def test_supervisor_uses_contained_cwd_and_sanitized_environment(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    source = dict(os.environ)
    source["AZURE_OPENAI_API_KEY"] = "must-not-leak"
    source["AGENTBUS_DAEMON_TOKEN"] = "must-not-leak"
    source["HOME"] = "developer-home"
    code = (
        "import json, os, pathlib; "
        "print(json.dumps({'cwd': str(pathlib.Path.cwd()), "
        "'home': os.environ.get('HOME'), "
        "'temp': os.environ.get('TEMP'), "
        "'azure': 'AZURE_OPENAI_API_KEY' in os.environ, "
        "'daemon': 'AGENTBUS_DAEMON_TOKEN' in os.environ}))"
    )

    result = _supervisor(tmp_path, source_environment=source).run(
        "python",
        ("-c", code),
        working_directory="nested",
    )
    payload = json.loads(result.stdout)

    assert payload["cwd"] == str(nested.resolve())
    assert payload["azure"] is False
    assert payload["daemon"] is False
    assert payload["home"] != "developer-home"
    assert payload["temp"] == payload["home"]
    assert Path(payload["home"]).exists() is False
    diagnostics = result.safe_diagnostic_metadata["environment"]
    assert "variable_names" in diagnostics
    assert "must-not-leak" not in repr(result.safe_diagnostic_metadata)


def test_supervisor_bounds_real_process_output_and_streams_events(
    tmp_path: Path,
) -> None:
    events: list[ToolOutputChunk] = []
    budget = ToolResourceBudget(
        stdout_bytes=20,
        stderr_bytes=20,
        combined_output_bytes=30,
    )

    result = _supervisor(tmp_path).run(
        "python",
        (
            "-c",
            "import sys; sys.stdout.buffer.write(b'x' * 50); "
            "sys.stderr.buffer.write(b'y' * 50)",
        ),
        resource_budget=budget,
        output_callback=events.append,
    )

    assert result.passed is True
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert result.resource_usage.stdout_bytes == 50
    assert result.resource_usage.stderr_bytes == 50
    assert len(result.stdout.encode("utf-8")) <= 20
    assert len(result.stderr.encode("utf-8")) <= 20
    assert events


def test_supervisor_enforces_timeout(tmp_path: Path) -> None:
    result = _supervisor(tmp_path).run(
        "python",
        ("-c", "import time; time.sleep(60)"),
        timeout_seconds=0.1,
    )

    assert result.passed is False
    assert result.timed_out is True
    assert result.cancelled is False
    assert result.termination_reason == "wall_clock_timeout"
    assert result.duration_seconds < 5


def test_supervisor_propagates_cancellation_to_running_process(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path)
    cancellation = CancellationToken()
    results = []
    process_ready = threading.Event()

    def observe_output(chunk: ToolOutputChunk) -> None:
        if "ready" in chunk.text:
            process_ready.set()

    thread = threading.Thread(
        target=lambda: results.append(
            supervisor.run(
                "python",
                ("-c", "import time; print('ready', flush=True); time.sleep(60)"),
                cancellation=cancellation,
                output_callback=observe_output,
                task_id="task-1",
            )
        )
    )
    thread.start()
    operation = cancellation.wait_for_active_operation(
        source="sandbox-process",
        timeout_seconds=2,
    )
    assert operation is not None
    try:
        assert process_ready.wait(timeout=2)
        cancellation.request("stop process")
        thread.join(timeout=5)
    finally:
        if thread.is_alive():
            cancellation.request("test cleanup")
            thread.join(timeout=5)

    assert thread.is_alive() is False
    assert len(results) == 1
    assert results[0].cancelled is True
    assert results[0].timed_out is False
    assert results[0].termination_reason == "cancellation_requested"
    state = cancellation.snapshot()
    assert state.acknowledged is True
    assert state.active_operations == []
    assert "process:python" not in state.operations_completed_after_request


def test_supervisor_does_not_launch_after_prior_cancellation(tmp_path: Path) -> None:
    cancellation = CancellationToken()
    cancellation.request("already cancelled")

    result = _supervisor(tmp_path).run(
        "python",
        ("-c", "raise SystemExit('must not execute')"),
        cancellation=cancellation,
    )

    assert result.cancelled is True
    assert result.pid is None
    assert result.termination_reason == "cancelled_before_launch"


def test_supervisor_cleans_descendants_after_parent_exits(tmp_path: Path) -> None:
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess, sys; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "print(child.pid, flush=True)"
    )

    result = _supervisor(tmp_path).run("python", ("-c", parent_code))
    child_pid = int(result.stdout.strip())

    assert result.passed is True
    deadline = time.monotonic() + 3
    while _pid_is_running(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert _pid_is_running(child_pid) is False


def test_supervisor_rejects_invalid_argument_shapes(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path)

    with pytest.raises(ProcessSupervisionError, match="sequence"):
        supervisor.run("python", "-c")
    with pytest.raises(ProcessSupervisionError, match="NUL"):
        supervisor.run("python", ("bad\x00argument",))


def _pid_is_running(pid: int) -> bool:
    if os.name != "nt":
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.is_file():
            try:
                if stat_path.read_text(encoding="utf-8").split()[2] == "Z":
                    return False
            except (OSError, IndexError):
                pass
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)
