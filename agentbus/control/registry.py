from __future__ import annotations

import ctypes
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentbus.control.errors import (
    ControlPlaneConflictError,
    ControlPlaneNotFoundError,
)
from agentbus.control.models import DaemonRegistryEntry

_REGISTRY_VERSION = 1
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_registry_path() -> Path:
    return (Path.home() / ".agentbus" / "daemons.json").resolve()


def executable_identity(pid: int | None = None) -> str:
    target_pid = os.getpid() if pid is None else pid
    if os.name == "nt":
        return _windows_executable(target_pid)
    if target_pid == os.getpid():
        return str(Path(sys.executable).resolve())
    executable = Path(f"/proc/{target_pid}/exe")
    try:
        return str(executable.resolve(strict=True))
    except OSError:
        return ""


def process_start_identity(pid: int | None = None) -> str:
    target_pid = os.getpid() if pid is None else pid
    if os.name == "nt":
        return _windows_process_start(target_pid)
    try:
        stat = Path(f"/proc/{target_pid}/stat").read_text(encoding="utf-8")
        start_ticks = stat.rsplit(")", maxsplit=1)[1].split()[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
    except (OSError, IndexError):
        return ""
    return f"{boot_id}:{start_ticks}"


def process_matches(entry: DaemonRegistryEntry) -> bool:
    return not _process_identity_mismatches(entry)


class DaemonRegistry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or default_registry_path()).expanduser().resolve()
        self._lock = threading.RLock()

    def list(self) -> list[DaemonRegistryEntry]:
        with self._lock:
            return self._read()

    def get(self, daemon_id: str) -> DaemonRegistryEntry:
        for entry in self.list():
            if entry.daemon_id == daemon_id:
                return entry
        raise ControlPlaneNotFoundError("The requested daemon is not registered.")

    def register(self, entry: DaemonRegistryEntry) -> None:
        with self._lock:
            entries = self._read()
            if any(item.daemon_id == entry.daemon_id for item in entries):
                raise ControlPlaneConflictError("The daemon is already registered.")
            entries.append(entry)
            self._write(entries)

    def heartbeat(self, daemon_id: str, at: datetime | None = None) -> None:
        with self._lock:
            entries = self._read()
            updated = False
            for index, entry in enumerate(entries):
                if entry.daemon_id == daemon_id:
                    entries[index] = entry.model_copy(
                        update={"heartbeat_at": at or utc_now()}
                    )
                    updated = True
                    break
            if not updated:
                raise ControlPlaneNotFoundError(
                    "The requested daemon is not registered."
                )
            self._write(entries)

    def remove(self, daemon_id: str) -> bool:
        with self._lock:
            entries = self._read()
            remaining = [entry for entry in entries if entry.daemon_id != daemon_id]
            if len(entries) == len(remaining):
                return False
            self._write(remaining)
            return True

    def cleanup_stale(self) -> list[str]:
        with self._lock:
            entries = self._read()
            active = [entry for entry in entries if process_matches(entry)]
            removed = [
                entry.daemon_id for entry in entries if entry.daemon_id not in {
                    item.daemon_id for item in active
                }
            ]
            if removed:
                self._write(active)
            return removed

    def _read(self) -> list[DaemonRegistryEntry]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("version") != _REGISTRY_VERSION:
                return []
            values = payload.get("daemons", [])
            return [DaemonRegistryEntry.model_validate(value) for value in values]
        except (OSError, ValueError, TypeError):
            return []

    def _write(self, entries: list[DaemonRegistryEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "version": _REGISTRY_VERSION,
            "daemons": [
                entry.model_dump(mode="json", exclude_none=True) for entry in entries
            ],
        }
        temporary = self.path.with_suffix(f"{self.path.suffix}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)


def terminate_registered_daemon(
    registry: DaemonRegistry,
    daemon_id: str,
) -> None:
    entry = registry.get(daemon_id)
    if not process_matches(entry):
        mismatches = _process_identity_mismatches(entry)
        detail = ", ".join(mismatches) or "identity mismatch"
        registry.remove(daemon_id)
        raise ControlPlaneConflictError(
            "The registered process identity no longer matches "
            f"({detail}); no process was stopped."
        )
    if entry.pid == os.getpid():
        raise ControlPlaneConflictError(
            "The current process cannot terminate itself through registry management."
        )
    os.kill(entry.pid, signal.SIGTERM)


def wait_for_registered_daemon_exit(
    registry: DaemonRegistry,
    daemon_id: str,
    *,
    timeout_seconds: float = 2.0,
    poll_seconds: float = 0.05,
) -> bool:
    """Remove a registration only after its identity-bound process has exited."""
    if timeout_seconds < 0 or poll_seconds <= 0:
        raise ValueError(
            "Daemon exit timeout must be non-negative and polling must be positive."
        )
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            entry = registry.get(daemon_id)
        except ControlPlaneNotFoundError:
            return True
        if not process_matches(entry):
            registry.remove(daemon_id)
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_seconds, remaining))


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32, handle = _open_windows_process(pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_executable(pid: int) -> str:
    from ctypes import wintypes

    kernel32, handle = _open_windows_process(pid)
    if not handle:
        return ""
    try:
        query_image = kernel32.QueryFullProcessImageNameW
        query_image.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        query_image.restype = wintypes.BOOL
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not query_image(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return str(Path(buffer.value).resolve())
    finally:
        kernel32.CloseHandle(handle)


def _windows_process_start(pid: int) -> str:
    from ctypes import wintypes

    kernel32, handle = _open_windows_process(pid)
    if not handle:
        return ""
    try:
        get_process_times = kernel32.GetProcessTimes
        get_process_times.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        get_process_times.restype = wintypes.BOOL
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        success = get_process_times(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not success:
            return ""
        value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return str(value)
    finally:
        kernel32.CloseHandle(handle)


def _open_windows_process(pid: int):
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    open_process.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    return kernel32, open_process(
        _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )


def _process_identity_mismatches(entry: DaemonRegistryEntry) -> list[str]:
    if not _process_exists(entry.pid):
        return ["process missing"]
    actual_start = process_start_identity(entry.pid)
    actual_executable = executable_identity(entry.pid)
    mismatches: list[str] = []
    if not actual_start or actual_start != entry.process_start_identity:
        mismatches.append("start identity")
    if not actual_executable or not _same_path(actual_executable, entry.executable):
        mismatches.append("executable identity")
    return mismatches
