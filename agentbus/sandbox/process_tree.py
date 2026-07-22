from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentbus.tools.protocol import ToolLimitUsage, ToolResourceBudget


@dataclass(frozen=True)
class ProcessTreeMetadata:
    backend: str
    process_group_id: int | None
    job_assigned: bool
    tree_termination_supported: bool
    limitation: str | None = None

    def safe_metadata(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "process_group_id": self.process_group_id,
            "job_assigned": self.job_assigned,
            "tree_termination_supported": self.tree_termination_supported,
            "limitation": self.limitation,
        }


class ManagedProcessTree:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        budget: ToolResourceBudget,
        *,
        job_handle: int | None = None,
        job_limitation: str | None = None,
    ) -> None:
        self._process = process
        self._budget = budget
        self._job_handle = job_handle
        self._lock = threading.Lock()
        self._closed = False
        self._termination_requested = False
        if os.name == "nt":
            taskkill_available = _windows_taskkill_path() is not None
            fallback_limitation = job_limitation
            if job_handle is None and not taskkill_available:
                fallback_limitation = (
                    "Windows Job Object assignment and taskkill fallback are unavailable; "
                    "only the direct process can be terminated."
                )
            self.metadata = ProcessTreeMetadata(
                backend="windows-job" if job_handle else "windows-taskkill-fallback",
                process_group_id=process.pid,
                job_assigned=job_handle is not None,
                tree_termination_supported=(
                    job_handle is not None or taskkill_available
                ),
                limitation=fallback_limitation,
            )
        else:
            self.metadata = ProcessTreeMetadata(
                backend="posix-process-group",
                process_group_id=process.pid,
                job_assigned=False,
                tree_termination_supported=True,
            )

    @classmethod
    def attach(
        cls,
        process: subprocess.Popen[bytes],
        budget: ToolResourceBudget,
    ) -> "ManagedProcessTree":
        if os.name != "nt":
            return cls(process, budget)
        handle, limitation = _create_windows_job(process, budget)
        return cls(
            process,
            budget,
            job_handle=handle,
            job_limitation=limitation,
        )

    @staticmethod
    def launch_options() -> dict[str, Any]:
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            return {"creationflags": flags}
        return {"start_new_session": True}

    def terminate(self, *, grace_seconds: float) -> None:
        with self._lock:
            if self._closed:
                return
            job_handle = self._job_handle
            self._termination_requested = True
        if os.name == "nt":
            if job_handle is not None:
                _terminate_windows_job(job_handle)
            else:
                _taskkill_tree(self._process.pid, timeout_seconds=grace_seconds + 2)
        else:
            _terminate_posix_group(self._process, grace_seconds=grace_seconds)
        try:
            self._process.wait(timeout=max(0.1, grace_seconds))
        except subprocess.TimeoutExpired:
            self._process.kill()
            try:
                self._process.wait(timeout=max(0.1, grace_seconds))
            except subprocess.TimeoutExpired:
                pass

    def close(self, *, grace_seconds: float = 0.5) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            job_handle = self._job_handle
            self._job_handle = None
            termination_requested = self._termination_requested
        if job_handle is not None:
            _close_windows_handle(job_handle)
        elif os.name != "nt" and not termination_requested:
            _terminate_posix_group(
                self._process,
                grace_seconds=grace_seconds,
            )

    def platform_limits(self) -> dict[str, ToolLimitUsage]:
        if os.name != "nt" or not self.metadata.job_assigned:
            return {}
        return {
            "child_processes": ToolLimitUsage(
                requested=self._budget.child_processes,
                supported=True,
                enforced=True,
                observed=None,
                diagnostic="Windows Job Object active-process limit.",
            ),
            "memory_bytes": ToolLimitUsage(
                requested=self._budget.memory_bytes,
                supported=True,
                enforced=self._budget.memory_bytes is not None,
                observed=None,
                diagnostic="Windows Job Object aggregate memory limit.",
            ),
            "cpu_seconds": ToolLimitUsage(
                requested=self._budget.cpu_seconds,
                supported=True,
                enforced=self._budget.cpu_seconds is not None,
                observed=None,
                diagnostic="Windows Job Object user-time limit.",
            ),
        }


def _terminate_posix_group(
    process: subprocess.Popen[bytes],
    *,
    grace_seconds: float,
) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + max(0.1, grace_seconds)
    while time.monotonic() < deadline:
        if not _posix_group_exists(process.pid):
            return
        time.sleep(0.02)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _posix_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _taskkill_tree(pid: int, *, timeout_seconds: float) -> None:
    taskkill = _windows_taskkill_path()
    if taskkill is None:
        return
    system_root = str(taskkill.parents[1])
    helper_environment = {
        "SystemRoot": system_root,
        "WINDIR": system_root,
    }
    try:
        subprocess.run(
            [str(taskkill), "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1.0, timeout_seconds),
            check=False,
            shell=False,
            env=helper_environment,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _create_windows_job(
    process: subprocess.Popen[bytes],
    budget: ToolResourceBudget,
) -> tuple[int | None, str | None]:
    if os.name != "nt":
        return None, None
    handle: int | None = None
    try:
        kernel32 = _windows_kernel32()
        raw_handle = kernel32.CreateJobObjectW(None, None)
        handle = int(raw_handle) if raw_handle else None
        if not handle:
            return None, "Windows Job Object creation failed; taskkill fallback active."
        information = _windows_job_information(budget)
        configured = kernel32.SetInformationJobObject(
            handle,
            9,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        process_handle = int(getattr(process, "_handle"))
        assigned = configured and kernel32.AssignProcessToJobObject(
            handle,
            process_handle,
        )
        if not assigned:
            kernel32.CloseHandle(handle)
            return None, "Windows Job Object assignment failed; taskkill fallback active."
        return handle, None
    except (AttributeError, OSError, TypeError, ValueError):
        if handle is not None:
            _close_windows_handle(handle)
        return None, "Windows Job Object setup failed; taskkill fallback active."


def _terminate_windows_job(handle: int) -> None:
    if os.name != "nt":
        return
    try:
        _windows_kernel32().TerminateJobObject(handle, 1)
    except OSError:
        return


def _close_windows_handle(handle: int) -> None:
    if os.name != "nt":
        return
    try:
        _windows_kernel32().CloseHandle(handle)
    except OSError:
        return


def _windows_taskkill_path() -> Path | None:
    if os.name != "nt":
        return None
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        return None
    try:
        taskkill = (Path(system_root) / "System32" / "taskkill.exe").resolve(
            strict=True
        )
    except OSError:
        return None
    return taskkill if taskkill.is_file() else None


if os.name == "nt":
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def _windows_job_information(budget: ToolResourceBudget) -> Any:
    information = _ExtendedLimitInformation()
    flags = 0x00002000 | 0x00000008
    information.BasicLimitInformation.ActiveProcessLimit = budget.child_processes + 1
    if budget.memory_bytes is not None:
        flags |= 0x00000200
        information.JobMemoryLimit = budget.memory_bytes
    if budget.cpu_seconds is not None:
        flags |= 0x00000004
        information.BasicLimitInformation.PerJobUserTimeLimit = int(
            budget.cpu_seconds * 10_000_000
        )
    information.BasicLimitInformation.LimitFlags = flags
    return information


def _windows_kernel32() -> Any:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32
