import os

import pytest


_AGENTBUS_ENV_PREFIXES = (
    "AGENTBUS_",
    "AZURE_OPENAI_",
)


@pytest.fixture(autouse=True)
def isolate_agentbus_environment(monkeypatch: pytest.MonkeyPatch):
    """Prevent the developer's local provider settings from leaking into tests."""

    for name in tuple(os.environ):
        if name.upper().startswith(_AGENTBUS_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)
