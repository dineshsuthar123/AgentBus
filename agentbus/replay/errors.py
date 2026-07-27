"""Safe failures raised by deterministic replay operations."""


class ReplayError(RuntimeError):
    """Base class for replay failures."""


class ReplayInputUnavailableError(ReplayError):
    """Raised when a requested replay lacks required captured inputs."""


class ReplayIncompatibleError(ReplayError):
    """Raised when trace, protocol, or policy versions cannot be replayed."""


class ReplayIsolationError(ReplayError):
    """Raised when replay workspace isolation cannot be established."""


class ReplayCancelledError(ReplayError):
    """Raised when a replay session is cooperatively cancelled."""


__all__ = [
    "ReplayCancelledError",
    "ReplayError",
    "ReplayIncompatibleError",
    "ReplayInputUnavailableError",
    "ReplayIsolationError",
]
