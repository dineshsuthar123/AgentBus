class EvaluationError(RuntimeError):
    """Base error for evaluation operations."""


class EvaluationConfigurationError(EvaluationError, ValueError):
    """Raised when a suite, case, variant, or budget is unsafe or invalid."""


class EvaluationBudgetExceeded(EvaluationError):
    """Raised before a provider call would exceed a local evaluation budget."""


class EvaluationStorageError(EvaluationError):
    """Raised when evaluation results or baselines cannot be stored safely."""


class FixtureOwnershipError(EvaluationError):
    """Raised when fixture cleanup cannot prove ownership."""


class ScriptedProviderError(EvaluationError):
    """Raised when a deterministic provider route has no exact scripted response."""
