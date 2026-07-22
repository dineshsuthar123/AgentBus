from __future__ import annotations

from pydantic import Field

from agentbus.policy.models import PolicyModel


class ToolPolicyConfiguration(PolicyModel):
    standard_executables: tuple[str, ...] = (
        "python",
        "python3",
        "pytest",
        "git",
        "node",
        "npm",
    )
    safe_environment_keys: tuple[str, ...] = (
        "CI",
        "LANG",
        "LC_ALL",
        "NO_COLOR",
        "PATH",
        "PATHEXT",
        "PYTHONDONTWRITEBYTECODE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    )
    automatic_path_limit: int = Field(default=32, ge=1, le=10_000)
    standard_wall_clock_seconds: float = Field(default=300.0, gt=0, le=86_400)
    standard_output_bytes: int = Field(default=262_144, ge=1, le=33_554_432)
    protected_file_names: tuple[str, ...] = (
        ".env",
        ".git-credentials",
        ".netrc",
        "credentials",
        "credentials.json",
        "daemons.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
        "state.db",
    )
    protected_suffixes: tuple[str, ...] = (
        ".key",
        ".p12",
        ".pfx",
        ".pem",
    )
    approval_path_prefixes: tuple[str, ...] = (
        ".github/workflows/",
        ".gitlab-ci",
        "deploy/",
        "deployment/",
        "infra/",
        "security/",
    )


DEFAULT_TOOL_POLICY = ToolPolicyConfiguration()
