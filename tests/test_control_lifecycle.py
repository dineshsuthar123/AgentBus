from __future__ import annotations

import time
from types import SimpleNamespace

from agentbus.control.lifecycle import IdleShutdownMonitor


def test_idle_shutdown_waits_until_active_work_finishes() -> None:
    active = True
    server = SimpleNamespace(should_exit=False)
    app = SimpleNamespace(
        state=SimpleNamespace(last_activity=time.monotonic() - 60)
    )
    monitor = IdleShutdownMonitor(
        server,
        app,
        timeout_seconds=1,
        has_active_work=lambda: active,
    )

    assert monitor.should_shutdown() is False

    active = False

    assert monitor.should_shutdown() is True
