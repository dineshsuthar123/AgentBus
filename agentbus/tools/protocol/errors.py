from __future__ import annotations


class ToolProtocolError(ValueError):
    """Base error for invalid or incompatible tool protocol messages."""


class ToolProtocolVersionError(ToolProtocolError):
    """Raised when a message requests an unsupported protocol version."""


class ToolProtocolValidationError(ToolProtocolError):
    """Raised when a tool protocol invariant is violated."""


class ToolCapabilityEscalationError(ToolProtocolValidationError):
    """Raised when an invocation expands an already-authorized capability set."""
