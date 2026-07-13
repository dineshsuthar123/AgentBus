from __future__ import annotations

from typing import Any

from agentbus.security.redaction import redact_text, sanitize_json


class ModelProviderError(RuntimeError):
    error_category = "provider_error"
    default_retryable = False
    default_fallback_eligible = False

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: str | None = None,
        retryable: bool | None = None,
        fallback_eligible: bool | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.provider = provider
        self.model = model
        self.retryable = (
            self.default_retryable if retryable is None else bool(retryable)
        )
        self.fallback_eligible = (
            self.default_fallback_eligible
            if fallback_eligible is None
            else bool(fallback_eligible)
        )
        self.http_status = http_status
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds
        self.metadata = sanitize_json(metadata or {})
        safe_message = redact_text(message, max_chars=2_000) or self.error_category
        self.safe_message = safe_message
        super().__init__(safe_message)

    def safe_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "error_category": self.error_category,
            "retryable": self.retryable,
            "fallback_eligible": self.fallback_eligible,
            "http_status": self.http_status,
            "request_id": self.request_id,
            "retry_after_seconds": self.retry_after_seconds,
            "metadata": self.metadata,
        }


class ModelConfigurationError(ModelProviderError):
    error_category = "configuration_error"


class ModelAuthenticationError(ModelProviderError):
    error_category = "authentication_error"


class ModelAuthorizationError(ModelProviderError):
    error_category = "authorization_error"


class ModelNotFoundError(ModelProviderError):
    error_category = "model_not_found"


class ModelRateLimitError(ModelProviderError):
    error_category = "rate_limit_error"
    default_retryable = True
    default_fallback_eligible = True


class ModelQuotaExceededError(ModelProviderError):
    error_category = "quota_exceeded"


class ModelTimeoutError(ModelProviderError):
    error_category = "timeout_error"
    default_retryable = True
    default_fallback_eligible = True


class ModelTransportError(ModelProviderError):
    error_category = "transport_error"
    default_retryable = True
    default_fallback_eligible = True


class ModelServiceUnavailableError(ModelProviderError):
    error_category = "service_unavailable"
    default_retryable = True
    default_fallback_eligible = True


class ModelBadRequestError(ModelProviderError):
    error_category = "bad_request"


class ModelOutputError(ModelProviderError, ValueError):
    error_category = "output_error"


class ModelSchemaValidationError(ModelOutputError):
    error_category = "schema_validation_error"


class ModelContentPolicyError(ModelProviderError):
    error_category = "content_policy_error"
