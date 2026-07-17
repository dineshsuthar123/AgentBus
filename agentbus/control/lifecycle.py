from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

from agentbus.control.models import DaemonRegistryEntry
from agentbus.control.registry import DaemonRegistry, utc_now


def bind_loopback_socket(host: str, port: int) -> socket.socket:
    if port < 0 or port > 65535:
        raise ValueError("port must be between 0 and 65535")
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(128)
        listener.set_inheritable(False)
        return listener
    except Exception:
        listener.close()
        raise


class DaemonHeartbeat:
    def __init__(
        self,
        registry: DaemonRegistry,
        daemon_id: str,
        *,
        interval_seconds: float = 5.0,
    ):
        self.registry = registry
        self.daemon_id = daemon_id
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="agentbus-daemon-heartbeat",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds * 2))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.registry.heartbeat(self.daemon_id, utc_now())
            except Exception:
                return


class IdleShutdownMonitor:
    def __init__(
        self,
        server,
        app,
        timeout_seconds: float,
        *,
        poll_seconds: float = 1.0,
    ):
        self.server = server
        self.app = app
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="agentbus-daemon-idle-monitor",
            daemon=True,
        )

    def start(self) -> None:
        if self.timeout_seconds > 0:
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self.poll_seconds * 2))

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            last_activity = float(getattr(self.app.state, "last_activity", 0.0))
            if time.monotonic() - last_activity >= self.timeout_seconds:
                self.server.should_exit = True
                return
