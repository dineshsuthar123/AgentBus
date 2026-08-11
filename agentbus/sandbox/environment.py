from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from agentbus.sandbox.errors import EnvironmentValidationError
from agentbus.security.redaction import is_sensitive_environment_key


_PASSTHROUGH_ENVIRONMENT = frozenset(
    {
        "LANG",
        "LC_ALL",
        "NO_COLOR",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
)
_DEFAULT_SAFE_OVERRIDES = frozenset(
    {
        "CI",
        "LANG",
        "LC_ALL",
        "NO_COLOR",
        "PYTHONDONTWRITEBYTECODE",
    }
)
_FIXED_ENVIRONMENT = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONNOUSERSITE": "1",
    "PYTHONUTF8": "1",
}


def sanitized_process_environment(
    *,
    source: Mapping[str, str] | None = None,
    executable_directories: tuple[str | Path, ...] = (),
    overrides: Mapping[str, str] | None = None,
    allowed_override_names: frozenset[str] = _DEFAULT_SAFE_OVERRIDES,
    isolated_home: str | Path | None = None,
) -> dict[str, str]:
    source_environment = os.environ if source is None else source
    environment: dict[str, str] = {}
    for name in sorted(_PASSTHROUGH_ENVIRONMENT):
        value = source_environment.get(name)
        if value and not is_sensitive_environment_key(name):
            environment[name] = value

    trusted_path = _trusted_path(executable_directories)
    if trusted_path:
        environment["PATH"] = trusted_path

    home = Path(isolated_home).resolve() if isolated_home is not None else None
    if home is not None:
        environment["HOME"] = str(home)
        environment["TEMP"] = str(home)
        environment["TMP"] = str(home)
        environment["TMPDIR"] = str(home)
        if os.name == "nt":
            environment["USERPROFILE"] = str(home)

    environment.update(_FIXED_ENVIRONMENT)
    for raw_name, raw_value in (overrides or {}).items():
        name = str(raw_name).strip().upper()
        value = str(raw_value)
        if not name or "=" in name or "\x00" in name or "\x00" in value:
            raise EnvironmentValidationError(
                "Environment override names and values must be non-empty and NUL-free."
            )
        if is_sensitive_environment_key(name):
            raise EnvironmentValidationError(
                f"Sensitive environment override is not allowed: {name}."
            )
        if name not in allowed_override_names:
            raise EnvironmentValidationError(
                f"Environment override is not allowlisted: {name}."
            )
        environment[name] = value
    return environment


def environment_diagnostics(environment: Mapping[str, str]) -> dict[str, object]:
    names = tuple(sorted(environment))
    return {
        "variable_names": names,
        "variable_count": len(names),
        "sensitive_variables_present": any(
            is_sensitive_environment_key(name) for name in names
        ),
    }


def _trusted_path(directories: tuple[str | Path, ...]) -> str:
    normalized: list[str] = []
    for raw in directories:
        value = str(Path(raw).expanduser().resolve())
        key = os.path.normcase(value)
        if all(os.path.normcase(existing) != key for existing in normalized):
            normalized.append(value)
    return os.pathsep.join(normalized)
