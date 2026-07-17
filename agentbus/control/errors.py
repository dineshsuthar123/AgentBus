from __future__ import annotations

from typing import Any


class ControlPlaneError(RuntimeError):
    """Base error carrying a stable, safe control-protocol response."""

    code = "control_plane_error"
    retryable = False
    status_code = 400

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.safe_message = message
        self.details = details or {}


class ControlPlaneConfigurationError(ControlPlaneError):
    code = "control_plane_configuration_error"


class ControlPlaneUnavailableError(ControlPlaneError):
    code = "control_plane_unavailable"
    retryable = True
    status_code = 503


class ControlPlaneNotFoundError(ControlPlaneError):
    code = "not_found"
    status_code = 404


class ControlPlaneConflictError(ControlPlaneError):
    code = "conflict"
    status_code = 409


class ControlPlaneForbiddenError(ControlPlaneError):
    code = "forbidden"
    status_code = 403
