from enum import Enum


class TraceEventType(str, Enum):
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    SPAN_STARTED = "span.started"
    SPAN_COMPLETED = "span.completed"
    CHECKPOINT_CREATED = "checkpoint.created"
    RECORDING_DEGRADED = "recording.degraded"
    TRACE_RECONCILED = "trace.reconciled"


__all__ = ["TraceEventType"]
