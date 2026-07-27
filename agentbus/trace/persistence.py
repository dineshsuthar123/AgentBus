from __future__ import annotations

from typing import TYPE_CHECKING

from agentbus.trace.models import Trace, TraceCheckpoint, TraceEvent, TraceSpan

if TYPE_CHECKING:
    from agentbus.execution.state_store import StateStore


class StateStoreTraceSink:
    """Trace sink backed by the run's existing durable SQLite store."""

    def __init__(self, store: StateStore):
        self.store = store

    def write_span(self, span: TraceSpan) -> None:
        self.store.record_trace_span(span)

    def write_event(self, event: TraceEvent) -> None:
        self.store.record_trace_event(event)

    def write_checkpoint(self, checkpoint: TraceCheckpoint) -> None:
        self.store.record_trace_checkpoint(checkpoint)

    def write_trace(self, trace: Trace) -> None:
        self.store.finalize_trace(trace)


__all__ = ["StateStoreTraceSink"]
