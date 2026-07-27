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


class ReplayConsentRequiredError(ReplayError):
    """Raised when a fork requests live behavior without explicit consent."""


class RegressionFixtureError(ReplayError):
    """Raised when a regression fixture is invalid or cannot be captured."""


class RegressionFixtureAssertionError(RegressionFixtureError):
    """Raised when fixture assertions contradict captured evidence."""


__all__ = [
    "RegressionFixtureAssertionError",
    "RegressionFixtureError",
    "ReplayCancelledError",
    "ReplayConsentRequiredError",
    "ReplayError",
    "ReplayIncompatibleError",
    "ReplayInputUnavailableError",
    "ReplayIsolationError",
]
