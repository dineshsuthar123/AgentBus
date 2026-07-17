from __future__ import annotations

import pytest

from agentbus.control.authentication import (
    BearerAuthenticator,
    generate_session_token,
    safe_error_message,
    validate_loopback_host,
    validate_origin,
)
from agentbus.control.errors import (
    ControlPlaneConfigurationError,
    ControlPlaneForbiddenError,
)


def test_session_token_has_cryptographic_entropy() -> None:
    first = generate_session_token()
    second = generate_session_token()

    assert len(first) >= 32
    assert first != second


@pytest.mark.parametrize(("host", "expected"), [("127.0.0.1", "127.0.0.1"), ("::1", "::1")])
def test_loopback_bind_is_accepted(host: str, expected: str) -> None:
    assert validate_loopback_host(host) == expected


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "localhost"])
def test_remote_or_ambiguous_bind_is_rejected(host: str) -> None:
    with pytest.raises(ControlPlaneConfigurationError, match="loopback"):
        validate_loopback_host(host)


def test_bearer_authentication_uses_header_only() -> None:
    token = generate_session_token()
    authenticator = BearerAuthenticator(token)

    authenticator.authenticate({"authorization": f"Bearer {token}"})

    with pytest.raises(ControlPlaneForbiddenError):
        authenticator.authenticate({})
    with pytest.raises(ControlPlaneForbiddenError):
        authenticator.authenticate({"authorization": "Bearer wrong-token"})


@pytest.mark.parametrize(
    "origin",
    [None, "http://127.0.0.1:3000", "https://[::1]:8443"],
)
def test_local_or_absent_origin_is_accepted(origin: str | None) -> None:
    validate_origin(origin)


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.com",
        "null",
        "vscode-webview://example",
        "http://192.168.1.20",
    ],
)
def test_untrusted_origin_is_rejected(origin: str) -> None:
    with pytest.raises(ControlPlaneForbiddenError, match="not trusted"):
        validate_origin(origin)


def test_error_message_redacts_bearer_tokens() -> None:
    token = generate_session_token()
    message = safe_error_message(RuntimeError(f"Authorization: Bearer {token}"))

    assert token not in message
    assert "[REDACTED]" in message
