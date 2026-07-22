from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from agentbus.tools.protocol import (
    ToolOutputChunk,
    ToolOutputStream,
    ToolResourceBudget,
    bound_text,
)
from agentbus.tools.protocol.models import MAX_INLINE_OUTPUT_CHARS


OutputCallback = Callable[[ToolOutputChunk], None]


@dataclass(frozen=True)
class ProcessOutputSnapshot:
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    retained_stdout_bytes: int
    retained_stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    output_events: int
    output_events_truncated: bool
    callback_failures: int


class BoundedProcessOutput:
    """Thread-safe process output capture with independent and combined budgets."""

    def __init__(
        self,
        budget: ToolResourceBudget,
        *,
        callback: OutputCallback | None = None,
        maximum_events: int = 1_024,
    ) -> None:
        if maximum_events < 0:
            raise ValueError("maximum_events must be non-negative")
        self._budget = budget
        self._callback = callback
        self._maximum_events = maximum_events
        self._lock = threading.RLock()
        self._buffers = {
            ToolOutputStream.STDOUT: bytearray(),
            ToolOutputStream.STDERR: bytearray(),
        }
        self._pending_events = {
            ToolOutputStream.STDOUT: bytearray(),
            ToolOutputStream.STDERR: bytearray(),
        }
        self._observed = {
            ToolOutputStream.STDOUT: 0,
            ToolOutputStream.STDERR: 0,
        }
        self._truncated = {
            ToolOutputStream.STDOUT: False,
            ToolOutputStream.STDERR: False,
        }
        self._sequence = 0
        self._event_count = 0
        self._events_truncated = False
        self._callback_failures = 0
        self._finalized = False

    def consume(self, stream: ToolOutputStream, data: bytes) -> None:
        if stream not in self._buffers:
            raise ValueError("Only stdout and stderr can be captured.")
        if not isinstance(data, bytes):
            raise TypeError("Process output chunks must be bytes.")
        if not data:
            return

        with self._lock:
            if self._finalized:
                raise RuntimeError("Process output capture is already finalized.")
            self._observed[stream] += len(data)
            accepted = self._accepted_bytes(stream, data)
            if len(accepted) != len(data):
                self._truncated[stream] = True
            self._buffers[stream].extend(accepted)
            if self._callback is not None:
                self._pending_events[stream].extend(accepted)
                events = self._complete_line_events(stream)
            else:
                events = []
        self._emit(events)

    def finalize(self) -> ProcessOutputSnapshot:
        with self._lock:
            if not self._finalized:
                events: list[ToolOutputChunk] = []
                for stream in (ToolOutputStream.STDOUT, ToolOutputStream.STDERR):
                    pending = bytes(self._pending_events[stream])
                    self._pending_events[stream].clear()
                    if pending or self._truncated[stream]:
                        events.extend(self._events_for_payload(stream, pending))
                self._finalized = True
            else:
                events = []
        self._emit(events)

        with self._lock:
            stdout_buffer = bytes(self._buffers[ToolOutputStream.STDOUT])
            stderr_buffer = bytes(self._buffers[ToolOutputStream.STDERR])
            stdout, stdout_text_truncated = _safe_decode(stdout_buffer)
            stderr, stderr_text_truncated = _safe_decode(stderr_buffer)
            return ProcessOutputSnapshot(
                stdout=stdout,
                stderr=stderr,
                stdout_bytes=self._observed[ToolOutputStream.STDOUT],
                stderr_bytes=self._observed[ToolOutputStream.STDERR],
                retained_stdout_bytes=len(stdout_buffer),
                retained_stderr_bytes=len(stderr_buffer),
                stdout_truncated=(
                    self._truncated[ToolOutputStream.STDOUT]
                    or stdout_text_truncated
                ),
                stderr_truncated=(
                    self._truncated[ToolOutputStream.STDERR]
                    or stderr_text_truncated
                ),
                output_events=self._event_count,
                output_events_truncated=self._events_truncated,
                callback_failures=self._callback_failures,
            )

    def _accepted_bytes(self, stream: ToolOutputStream, data: bytes) -> bytes:
        stream_limit = (
            self._budget.stdout_bytes
            if stream == ToolOutputStream.STDOUT
            else self._budget.stderr_bytes
        )
        stream_remaining = max(0, stream_limit - len(self._buffers[stream]))
        combined_retained = sum(len(buffer) for buffer in self._buffers.values())
        combined_remaining = max(
            0,
            self._budget.combined_output_bytes - combined_retained,
        )
        return data[: min(len(data), stream_remaining, combined_remaining)]

    def _complete_line_events(
        self,
        stream: ToolOutputStream,
    ) -> list[ToolOutputChunk]:
        pending = self._pending_events[stream]
        last_newline = pending.rfind(b"\n")
        if last_newline < 0:
            return []
        payload = bytes(pending[: last_newline + 1])
        del pending[: last_newline + 1]
        return self._events_for_payload(stream, payload)

    def _events_for_payload(
        self,
        stream: ToolOutputStream,
        payload: bytes,
    ) -> list[ToolOutputChunk]:
        if self._callback is None:
            return []
        safe_text, text_truncated = _safe_decode(payload)
        if text_truncated:
            self._truncated[stream] = True
            self._events_truncated = True
        if not safe_text and not self._truncated[stream]:
            return []
        pieces = [
            safe_text[index : index + MAX_INLINE_OUTPUT_CHARS]
            for index in range(0, len(safe_text), MAX_INLINE_OUTPUT_CHARS)
        ] or [""]
        events: list[ToolOutputChunk] = []
        for piece in pieces:
            if self._event_count >= self._maximum_events:
                self._events_truncated = True
                break
            self._sequence += 1
            self._event_count += 1
            events.append(
                ToolOutputChunk(
                    sequence=self._sequence,
                    stream=stream,
                    text=piece,
                    byte_count=len(piece.encode("utf-8")),
                    truncated=self._truncated[stream],
                )
            )
        return events

    def _emit(self, events: list[ToolOutputChunk]) -> None:
        if self._callback is None:
            return
        for event in events:
            try:
                self._callback(event)
            except Exception:
                with self._lock:
                    self._callback_failures += 1


def _safe_decode(value: bytes) -> tuple[str, bool]:
    decoded = value.decode("utf-8", errors="replace")
    text, _, truncated = bound_text(decoded, MAX_INLINE_OUTPUT_CHARS)
    return text, truncated
