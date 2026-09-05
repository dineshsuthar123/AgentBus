from __future__ import annotations

import os
import warnings
from collections.abc import Mapping
from pathlib import Path


PRODUCT_NAME = "Syndra"
STUDIO_NAME = "Syndra Studio"
CANONICAL_ENV_PREFIX = "SYNDRA_"
LEGACY_ENV_PREFIX = "AGENTBUS_"
CANONICAL_STATE_DIRECTORY = ".syndra"
LEGACY_STATE_DIRECTORY = ".agentbus"


class LegacyConfigurationWarning(UserWarning):
    """Warn about deterministic use of conflicting legacy configuration."""


def environment_names(name: str) -> tuple[str, str | None]:
    if name.startswith(CANONICAL_ENV_PREFIX):
        suffix = name.removeprefix(CANONICAL_ENV_PREFIX)
        return name, f"{LEGACY_ENV_PREFIX}{suffix}"
    if name.startswith(LEGACY_ENV_PREFIX):
        suffix = name.removeprefix(LEGACY_ENV_PREFIX)
        return f"{CANONICAL_ENV_PREFIX}{suffix}", name
    return name, None


def environment_value(
    name: str,
    environ: Mapping[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Return a canonical product variable before its legacy fallback."""

    environment = os.environ if environ is None else environ
    canonical, legacy = environment_names(name)
    canonical_value = _nonempty(environment.get(canonical))
    legacy_value = _nonempty(environment.get(legacy)) if legacy else None
    if (
        canonical_value is not None
        and legacy_value is not None
        and canonical_value != legacy_value
    ):
        warnings.warn(
            f"Conflicting {canonical} and legacy {legacy}; {canonical} takes precedence.",
            LegacyConfigurationWarning,
            stacklevel=2,
        )
    if canonical_value is not None:
        return canonical_value, canonical
    if legacy_value is not None:
        return legacy_value, legacy
    return None, None


def canonical_workspace_state_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / CANONICAL_STATE_DIRECTORY


def legacy_workspace_state_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / LEGACY_STATE_DIRECTORY


def discover_workspace_state_path(workspace: str | Path) -> Path:
    """Prefer Syndra state while keeping an existing AgentBus workspace readable."""

    canonical = canonical_workspace_state_path(workspace)
    legacy = legacy_workspace_state_path(workspace)
    if canonical.exists() or not legacy.exists():
        return canonical
    return legacy


def discover_compatible_path(canonical: Path, legacy: Path) -> Path:
    canonical_path = canonical.expanduser()
    legacy_path = legacy.expanduser()
    if canonical_path.exists() or not legacy_path.exists():
        return canonical_path
    return legacy_path


def _nonempty(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
