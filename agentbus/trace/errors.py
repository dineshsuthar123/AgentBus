"""Trace-domain failures exposed by recording, storage, and replay."""


class TraceError(RuntimeError):
    """Base class for safe, user-facing trace failures."""


class TraceValidationError(TraceError):
    """Raised when trace relationships violate deterministic invariants."""


class TraceNotFoundError(TraceError):
    """Raised when a requested trace record does not exist."""


class TraceIntegrityError(TraceError):
    """Raised when persisted trace material fails integrity validation."""


__all__ = [
    "TraceError",
    "TraceIntegrityError",
    "TraceNotFoundError",
    "TraceValidationError",
]
