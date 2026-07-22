from __future__ import annotations

from pathlib import Path

import pytest

from agentbus.sandbox import (
    EnvironmentValidationError,
    environment_diagnostics,
    sanitized_process_environment,
)


def test_environment_excludes_provider_secrets_and_developer_profile(
    tmp_path: Path,
) -> None:
    source = {
        "SYSTEMROOT": "C:/Windows",
        "PATH": "C:/untrusted",
        "HOME": "C:/Users/developer",
        "USERPROFILE": "C:/Users/developer",
        "AZURE_OPENAI_API_KEY": "secret",
        "AGENTBUS_DAEMON_TOKEN": "secret",
        "PIP_INDEX_URL": "https://user:password@example.invalid",
    }
    environment = sanitized_process_environment(
        source=source,
        executable_directories=(tmp_path / "bin",),
        isolated_home=tmp_path / "home",
    )

    assert environment["SYSTEMROOT"] == "C:/Windows"
    assert environment["PATH"] == str((tmp_path / "bin").resolve())
    assert environment["HOME"] == str((tmp_path / "home").resolve())
    assert "AZURE_OPENAI_API_KEY" not in environment
    assert "AGENTBUS_DAEMON_TOKEN" not in environment
    assert "PIP_INDEX_URL" not in environment
    assert "C:/Users/developer" not in environment.values()
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_environment_accepts_only_explicit_safe_overrides(tmp_path: Path) -> None:
    environment = sanitized_process_environment(
        source={},
        executable_directories=(tmp_path,),
        overrides={"CI": "1", "NO_COLOR": "1"},
    )
    assert environment["CI"] == "1"
    assert environment["NO_COLOR"] == "1"

    with pytest.raises(EnvironmentValidationError, match="not allowlisted"):
        sanitized_process_environment(source={}, overrides={"CUSTOM": "value"})
    with pytest.raises(EnvironmentValidationError, match="Sensitive"):
        sanitized_process_environment(source={}, overrides={"API_KEY": "secret"})


def test_environment_diagnostics_never_include_values() -> None:
    diagnostic = environment_diagnostics({"PATH": "secret-value", "CI": "1"})

    assert diagnostic == {
        "variable_names": ("CI", "PATH"),
        "variable_count": 2,
        "sensitive_variables_present": False,
    }
    assert "secret-value" not in repr(diagnostic)
