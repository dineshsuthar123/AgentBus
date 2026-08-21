from __future__ import annotations

from agentbus.intelligence.errors import (
    IndexUnavailableError,
    RepositoryIntelligenceError,
    UnsafeRepositoryPathError,
)
from agentbus.security.redaction import redact_text
from agentbus.tools.filesystem_security import FileSystemSecurityError
from agentbus.validation.models import FailureCategory, ValidationFailure


class ValidationError(RuntimeError):
    """Base error for bounded validation failures."""


class ManifestValidationError(ValidationError):
    pass


class RepositoryValidationError(ValidationError):
    pass


class ResourceLimitExceeded(ValidationError):
    pass


class ScenarioValidationError(ValidationError):
    pass


def classify_failure(
    error: BaseException,
    *,
    repository_id: str | None = None,
    scenario_id: str | None = None,
) -> ValidationFailure:
    if isinstance(error, ResourceLimitExceeded):
        category = FailureCategory.RESOURCE
    elif isinstance(error, (FileSystemSecurityError, UnsafeRepositoryPathError)):
        category = FailureCategory.CONTAINMENT
    elif isinstance(error, (IndexUnavailableError, RepositoryIntelligenceError)):
        category = FailureCategory.INDEXING
    elif isinstance(error, ManifestValidationError):
        category = FailureCategory.MANIFEST
    elif isinstance(error, ScenarioValidationError):
        category = FailureCategory.UNKNOWN
    elif isinstance(error, (OSError, RepositoryValidationError)):
        category = FailureCategory.REPOSITORY
    else:
        category = FailureCategory.UNKNOWN
    error_name = type(error).__name__[:128]
    detail = _bounded_safe_text(str(error)) or error_name
    return ValidationFailure(
        category=category,
        summary=f"{error_name} during bounded repository validation.",
        detail=detail,
        repository_id=repository_id,
        scenario_id=scenario_id,
        fatal=True,
        retryable=False,
    )


def _bounded_safe_text(value: str, maximum: int = 2_048) -> str:
    safe = redact_text(value).replace("\x00", "\\0")
    if len(safe) <= maximum:
        return safe
    return safe[: maximum - 15] + "...[truncated]"
