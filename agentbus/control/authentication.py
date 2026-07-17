from __future__ import annotations

import ipaddress
import secrets
from collections.abc import Mapping
from urllib.parse import urlsplit

from agentbus.control.errors import (
    ControlPlaneConfigurationError,
    ControlPlaneForbiddenError,
)
from agentbus.security.redaction import redact_text

MINIMUM_TOKEN_BYTES = 32


def generate_session_token() -> str:
    return secrets.token_urlsafe(MINIMUM_TOKEN_BYTES)


def validate_loopback_host(host: str) -> str:
    candidate = host.strip().strip("[]")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError as exc:
        raise ControlPlaneConfigurationError(
            "The control plane host must be a numeric loopback address."
        ) from exc
    if not address.is_loopback:
        raise ControlPlaneConfigurationError(
            "The control plane may bind only to a loopback address."
        )
    return address.compressed


def extract_bearer_token(headers: Mapping[str, str]) -> str:
    value = headers.get("authorization", "")
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise ControlPlaneForbiddenError("Bearer authentication is required.")
    return token.strip()


class BearerAuthenticator:
    def __init__(self, token: str):
        if len(token) < MINIMUM_TOKEN_BYTES:
            raise ControlPlaneConfigurationError(
                "The control-plane session token is too short."
            )
        self._token = token

    def authenticate(self, headers: Mapping[str, str]) -> None:
        supplied = extract_bearer_token(headers)
        if not secrets.compare_digest(supplied, self._token):
            raise ControlPlaneForbiddenError("Bearer authentication failed.")


def validate_origin(origin: str | None) -> None:
    if origin is None:
        return
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname
        address = ipaddress.ip_address(host or "")
    except ValueError as exc:
        raise ControlPlaneForbiddenError("The request Origin is not trusted.") from exc
    if parsed.scheme not in {"http", "https"} or not address.is_loopback:
        raise ControlPlaneForbiddenError("The request Origin is not trusted.")


def safe_error_message(error: BaseException) -> str:
    return redact_text(str(error)) or "The control-plane request failed."
