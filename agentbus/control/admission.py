"""Bound request work and long-lived streams in the single-user local daemon."""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

from starlette.responses import JSONResponse


class ControlAdmission:
    def __init__(self, *, requests_per_second: float = 100, burst: int = 256,
                 max_requests: int = 64, max_streams: int = 32,
                 clock: Callable[[], float] = time.monotonic):
        if (not math.isfinite(requests_per_second) or requests_per_second <= 0
                or min(burst, max_requests, max_streams) < 1):
            raise ValueError("Control admission limits must be positive and finite.")
        self.rate, self.burst = requests_per_second, burst
        self.max_requests, self.max_streams = max_requests, max_streams
        self.clock = clock
        self._updated, self._tokens = clock(), float(burst)
        self._requests = self._streams = 0
        self._lock = threading.Lock()

    def acquire(self, *, stream: bool) -> int | None:
        with self._lock:
            now = self.clock()
            self._tokens = min(self.burst, self._tokens + max(0, now - self._updated) * self.rate)
            self._updated = now
            if self._tokens < 1:
                return 429
            self._tokens -= 1
            # Separate pools ensure live SSE clients cannot starve REST actions.
            if stream:
                if self._streams >= self.max_streams:
                    return 503
                self._streams += 1
            else:
                if self._requests >= self.max_requests:
                    return 503
                self._requests += 1
            return None

    def release(self, *, stream: bool) -> None:
        with self._lock:
            if stream:
                self._streams -= 1
            else:
                self._requests -= 1


class ControlAdmissionMiddleware:
    def __init__(self, app, *, admission: ControlAdmission):
        self.app, self.admission = app, admission

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        stream = scope.get("path", "").endswith("/events")
        status = self.admission.acquire(stream=stream)
        if status is not None:
            response = JSONResponse(
                {"error": {"code": "control_capacity", "message":
                           "Local control capacity reached. Retry after active requests finish.",
                           "retryable": True, "details": {}}},
                status_code=status,
                headers={"Retry-After": "1", "Cache-Control": "no-store",
                         "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"},
            )
            return await response(scope, receive, send)
        try:
            # Hold the slot through the final body, including SSE/disconnects.
            return await self.app(scope, receive, send)
        finally:
            self.admission.release(stream=stream)
