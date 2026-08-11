from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import agentbus.sandbox.process as process_module
from agentbus.execution.cancellation import CancellationToken
from agentbus.sandbox import ControlledProcessSupervisor, ExecutableCatalog
from agentbus.tools.protocol import ToolOutputChunk, ToolResourceBudget


FIXTURE = Path(__file__).parent / "fixtures" / "sandbox_process_peer.py"


def supervisor(root: Path) -> ControlledProcessSupervisor:
    return ControlledProcessSupervisor(
        root,
        catalog=ExecutableCatalog(
            {"process-peer": (sys.executable, "-u", str(FIXTURE))}
        ),
        poll_interval_seconds=0.01,
        termination_grace_seconds=0.1,
    )


def mode_arguments(
    mode: str,
    lifecycle: Path | None = None,
    *extra: str,
) -> tuple[str, ...]:
    arguments = ["--mode", mode]
    if lifecycle is not None:
        arguments.extend(["--lifecycle-dir", str(lifecycle)])
    arguments.extend(extra)
    return tuple(arguments)


def wait_for_roles(
    lifecycle: Path,
    roles: tuple[str, ...],
    *,
    timeout_seconds: float = 3.0,
) -> dict[str, int]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        found: dict[str, int] = {}
        for role in roles:
            markers = list(lifecycle.glob(f"{role}-*.pid"))
            if markers:
                found[role] = int(
                    markers[0].read_text(encoding="utf-8").strip()
                )
        if len(found) == len(roles):
            return found
        time.sleep(0.02)
    raise AssertionError(f"controlled process roles did not start: {roles}")


def assert_process_exits(pid: int, *, timeout_seconds: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while _pid_is_running(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert _pid_is_running(pid) is False


@pytest.mark.parametrize("mode", ("ignore-termination", "wait"))
def test_timeout_escalation_terminates_resistant_process(
    tmp_path: Path,
    mode: str,
) -> None:
    lifecycle = tmp_path / "lifecycle"
    lifecycle.mkdir()

    result = supervisor(tmp_path).run(
        "process-peer",
        mode_arguments(mode, lifecycle),
        timeout_seconds=0.15,
    )

    assert result.timed_out is True
    assert result.cancelled is False
    assert result.termination_reason == "wall_clock_timeout"
    assert result.passed is False
    assert result.pid is not None
    assert_process_exits(result.pid)


def test_cancellation_terminates_child_and_grandchild_tree(
    tmp_path: Path,
) -> None:
    lifecycle = tmp_path / "lifecycle"
    lifecycle.mkdir()
    cancellation = CancellationToken()
    ready = threading.Event()
    results = []

    def observe(chunk: ToolOutputChunk) -> None:
        if "tree-ready" in chunk.text:
            ready.set()

    thread = threading.Thread(
        target=lambda: results.append(
            supervisor(tmp_path).run(
                "process-peer",
                mode_arguments("tree", lifecycle),
                cancellation=cancellation,
                output_callback=observe,
                task_id="tree-task",
            )
        )
    )
    thread.start()
    try:
        assert ready.wait(timeout=3)
        pids = wait_for_roles(
            lifecycle,
            ("parent", "child", "grandchild"),
        )
        cancellation.request("cancel controlled process tree")
        thread.join(timeout=5)
    finally:
        if thread.is_alive():
            cancellation.request("test cleanup")
            thread.join(timeout=5)

    assert thread.is_alive() is False
    assert results[0].cancelled is True
    assert results[0].timed_out is False
    assert results[0].termination_reason == "cancellation_requested"
    assert cancellation.snapshot().acknowledged is True
    for pid in pids.values():
        assert_process_exits(pid)


def test_continuous_stdout_and_stderr_remain_bounded(tmp_path: Path) -> None:
    lifecycle = tmp_path / "lifecycle"
    lifecycle.mkdir()
    budget = ToolResourceBudget(
        stdout_bytes=2_048,
        stderr_bytes=1_536,
        combined_output_bytes=3_000,
    )

    result = supervisor(tmp_path).run(
        "process-peer",
        mode_arguments("continuous-output", lifecycle),
        timeout_seconds=0.15,
        resource_budget=budget,
    )

    assert result.timed_out is True
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert len(result.stdout.encode("utf-8")) <= budget.stdout_bytes
    assert len(result.stderr.encode("utf-8")) <= budget.stderr_bytes
    retained = len(result.stdout.encode("utf-8")) + len(
        result.stderr.encode("utf-8")
    )
    assert retained <= budget.combined_output_bytes
    assert result.resource_usage.stdout_bytes > budget.stdout_bytes
    assert result.resource_usage.stderr_bytes > budget.stderr_bytes
    assert result.pid is not None
    assert_process_exits(result.pid)


def test_repeated_child_creation_is_enforced_or_reported_and_cleaned(
    tmp_path: Path,
) -> None:
    lifecycle = tmp_path / "lifecycle"
    lifecycle.mkdir()
    budget = ToolResourceBudget(child_processes=2)

    result = supervisor(tmp_path).run(
        "process-peer",
        mode_arguments(
            "spawn-repeatedly",
            lifecycle,
            "--child-count",
            "8",
        ),
        resource_budget=budget,
    )

    assert result.passed is True
    payload = json.loads(result.stdout)
    child_limit = result.resource_usage.limits["child_processes"]
    if result.process_tree is not None and result.process_tree.job_assigned:
        assert len(payload["children"]) <= budget.child_processes
        assert child_limit.supported is True
        assert child_limit.enforced is True
    else:
        assert child_limit.supported is False
        assert child_limit.enforced is False
    for pid in payload["children"]:
        assert_process_exits(pid)


@pytest.mark.parametrize("exit_code", (3, 42, 255))
def test_unusual_early_exit_codes_are_preserved(
    tmp_path: Path,
    exit_code: int,
) -> None:
    result = supervisor(tmp_path).run(
        "process-peer",
        mode_arguments("exit", None, "--exit-code", str(exit_code)),
    )

    assert result.exit_code == exit_code
    assert result.passed is False
    assert result.timed_out is False
    assert result.cancelled is False
    assert result.termination_reason is None


def test_unicode_and_unexpected_pipe_closure_are_safe(tmp_path: Path) -> None:
    unicode_result = supervisor(tmp_path).run(
        "process-peer",
        mode_arguments("unicode"),
    )
    closed_result = supervisor(tmp_path).run(
        "process-peer",
        mode_arguments("close-pipes"),
    )

    assert unicode_result.passed is True
    assert "caf\u00e9" in unicode_result.stdout
    assert "\u6e2c\u8a66" in unicode_result.stdout
    assert "na\u00efve" in unicode_result.stderr
    assert closed_result.passed is True
    assert closed_result.stdout == ""
    assert closed_result.stderr == ""


def test_launch_contract_pins_interpreter_and_closes_inherited_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_popen = subprocess.Popen
    calls: list[tuple[object, dict[str, object]]] = []

    def recording_popen(command, *args, **kwargs):
        calls.append((command, kwargs))
        return original_popen(command, *args, **kwargs)

    monkeypatch.setattr(process_module.subprocess, "Popen", recording_popen)

    result = supervisor(tmp_path).run(
        "process-peer",
        mode_arguments("exit"),
    )

    assert result.passed is True
    assert result.executable.path == Path(sys.executable).resolve()
    assert result.executable.argument_prefix == ("-u", str(FIXTURE))
    assert len(calls) == 1
    command, options = calls[0]
    assert isinstance(command, list)
    assert Path(command[0]).is_absolute()
    assert options["shell"] is False
    assert options["close_fds"] is True
    assert result.process_tree is not None
    if os.name == "nt":
        assert result.process_tree.backend in {
            "windows-job",
            "windows-taskkill-fallback",
        }
        assert options["creationflags"]
    else:
        assert result.process_tree.backend == "posix-process-group"
        assert result.process_tree.process_group_id == result.pid
        assert options["start_new_session"] is True


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
