"""Versioned execution tracing primitives for AgentBus."""

from agentbus.trace.context import (
    TraceContext,
    copy_trace_context,
    current_trace_context,
    reset_trace_context,
    set_trace_context,
    trace_context,
)
from agentbus.trace.errors import (
    TraceError,
    TraceIntegrityError,
    TraceNotFoundError,
    TraceRecordingError,
    TraceValidationError,
)
from agentbus.trace.events import TraceEventType
from agentbus.trace.models import (
    ReplayMode,
    Trace,
    TraceArtifactReference,
    TraceCheckpoint,
    TraceEvent,
    TraceFailure,
    TraceInput,
    TraceLink,
    TraceLinkType,
    TraceOutput,
    TraceReplayMetadata,
    TraceResourceUsage,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
    TraceValueReference,
)
from agentbus.trace.version import TRACE_SCHEMA_NAME, TRACE_SCHEMA_VERSION
from agentbus.trace.recorder import TraceRecorder, TraceSink
from agentbus.trace.spans import (
    DeterministicSequence,
    trace_id_for_run,
    trace_item_id,
)

__all__ = [
    "ReplayMode",
    "TRACE_SCHEMA_NAME",
    "TRACE_SCHEMA_VERSION",
    "DeterministicSequence",
    "Trace",
    "TraceArtifactReference",
    "TraceCheckpoint",
    "TraceContext",
    "TraceError",
    "TraceEvent",
    "TraceEventType",
    "TraceFailure",
    "TraceInput",
    "TraceIntegrityError",
    "TraceLink",
    "TraceLinkType",
    "TraceNotFoundError",
    "TraceOutput",
    "TraceReplayMetadata",
    "TraceRecorder",
    "TraceRecordingError",
    "TraceResourceUsage",
    "TraceSpan",
    "TraceSpanType",
    "TraceStatus",
    "TraceSink",
    "TraceValidationError",
    "TraceValueReference",
    "copy_trace_context",
    "current_trace_context",
    "reset_trace_context",
    "set_trace_context",
    "trace_context",
    "trace_id_for_run",
    "trace_item_id",
]
