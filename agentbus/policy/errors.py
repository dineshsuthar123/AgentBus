from __future__ import annotations


class ToolPolicyError(RuntimeError):
    """Base error for deterministic tool authorization failures."""


class ToolPolicyConfigurationError(ToolPolicyError):
    """Raised when policy configuration is internally inconsistent."""


class ToolApprovalBindingError(ToolPolicyError):
    """Raised when an approval does not match the current invocation."""
