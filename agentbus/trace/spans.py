from __future__ import annotations

import hashlib
import threading

from agentbus.trace.errors import TraceRecordingError


class DeterministicSequence:
    """Thread-safe monotonic ordering shared by every trace item."""

    def __init__(self, *, next_value: int = 1):
        if next_value < 1:
            raise ValueError("Trace sequences must start at one or greater.")
        self._next_value = next_value
        self._lock = threading.Lock()

    @property
    def next_value(self) -> int:
        with self._lock:
            return self._next_value

    def claim(self) -> int:
        return self.reserve(1).start

    def reserve(self, count: int) -> range:
        if count < 1:
            raise ValueError("A trace sequence reservation must contain an item.")
        with self._lock:
            start = self._next_value
            self._next_value += count
        return range(start, start + count)


def trace_id_for_run(run_id: str) -> str:
    if not run_id:
        raise TraceRecordingError("A run ID is required to derive a trace ID.")
    digest = hashlib.sha256(f"agentbus-trace:v1:{run_id}".encode("utf-8")).hexdigest()
    return f"tr-{digest[:32]}"


def trace_item_id(trace_id: str, sequence: int, kind: str) -> str:
    if sequence < 1:
        raise TraceRecordingError("Trace item sequences must be positive.")
    digest = hashlib.sha256(
        f"{trace_id}:{sequence}:{kind}".encode("utf-8")
    ).hexdigest()
    return f"{kind[:12]}-{digest[:24]}"


__all__ = ["DeterministicSequence", "trace_id_for_run", "trace_item_id"]
