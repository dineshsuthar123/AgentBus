from __future__ import annotations


class SandboxError(RuntimeError):
    """Base error for managed local process execution."""


class SandboxValidationError(SandboxError):
    """Raised before process creation when a sandbox invariant fails."""


class ExecutableValidationError(SandboxValidationError):
    """Raised when an executable is not allowlisted or its identity changed."""


class WorkingDirectoryValidationError(SandboxValidationError):
    """Raised when a process working directory escapes its assigned worktree."""


class EnvironmentValidationError(SandboxValidationError):
    """Raised when an environment override is not explicitly safe."""


class ProcessSupervisionError(SandboxError):
    """Raised when a managed process cannot be created or supervised safely."""
