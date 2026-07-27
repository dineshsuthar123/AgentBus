"""Trace-domain failures exposed by recording, storage, and replay."""


class TraceError(RuntimeError):
    """Base class for safe, user-facing trace failures."""


class TraceValidationError(TraceError):
    """Raised when trace relationships violate deterministic invariants."""


class TraceNotFoundError(TraceError):
    """Raised when a requested trace record does not exist."""


class TraceIntegrityError(TraceError):
    """Raised when persisted trace material fails integrity validation."""


class TraceRecordingError(TraceError):
    """Raised when deterministic trace recording cannot continue safely."""


class TraceStorageError(TraceError):
    """Raised when sanitized trace material cannot be stored safely."""


class TraceObjectTooLargeError(TraceStorageError):
    """Raised when a trace object exceeds its configured storage bound."""


class TraceSecretRejectedError(TraceStorageError):
    """Raised when unredacted secret-classified material reaches storage."""


class TraceArchiveError(TraceError):
    """Raised when a portable trace archive is invalid or unsafe."""


class TraceArchiveConsentRequiredError(TraceArchiveError):
    """Raised before importing source-like archive content without consent."""


__all__ = [
    "TraceArchiveConsentRequiredError",
    "TraceArchiveError",
    "TraceError",
    "TraceIntegrityError",
    "TraceNotFoundError",
    "TraceObjectTooLargeError",
    "TraceRecordingError",
    "TraceSecretRejectedError",
    "TraceStorageError",
    "TraceValidationError",
]
