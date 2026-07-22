from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from agentbus.security.redaction import is_sensitive_environment_key
from agentbus.tools.protocol import CapabilityScope, ToolCapability, ToolCapabilityName


SUPPORTED_MCP_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")
LATEST_MCP_PROTOCOL_VERSION = SUPPORTED_MCP_PROTOCOL_VERSIONS[0]
MAX_MCP_ARGUMENTS = 64
MAX_MCP_ARGUMENT_CHARS = 4_096
MAX_MCP_ENVIRONMENT_ENTRIES = 32
MAX_MCP_TOOLS = 256

_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_ENVIRONMENT_OVERRIDES = frozenset(
    {"CI", "LANG", "LC_ALL", "NO_COLOR", "PYTHONDONTWRITEBYTECODE"}
)


class McpTransportKind(str, Enum):
    STDIO = "stdio"
    LOOPBACK_HTTP = "loopback_http"


class McpServerConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )

    server_id: str = Field(min_length=1, max_length=64)
    transport: McpTransportKind
    executable_alias: str | None = Field(default=None, min_length=1, max_length=64)
    arguments: tuple[str, ...] = ()
    working_directory: str = Field(default=".", min_length=1, max_length=2_048)
    environment: dict[str, str] = Field(default_factory=dict)
    inherit_environment: bool = False
    endpoint_url: str | None = Field(default=None, min_length=1, max_length=2_048)
    authorization_token: SecretStr | None = Field(default=None, repr=False)
    explicit_loopback_http: bool = False
    startup_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    request_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    maximum_server_output_bytes: int = Field(
        default=262_144,
        ge=1_024,
        le=4_194_304,
    )
    maximum_tool_output_bytes: int = Field(
        default=1_048_576,
        ge=1_024,
        le=16_777_216,
    )
    supported_protocol_versions: tuple[str, ...] = SUPPORTED_MCP_PROTOCOL_VERSIONS
    capability_map: dict[str, tuple[ToolCapability, ...]]

    @field_validator("server_id")
    @classmethod
    def server_id_is_safe(cls, value: str) -> str:
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(
                "MCP server IDs must use lowercase letters, digits, dashes, or underscores"
            )
        return value

    @field_validator("executable_alias")
    @classmethod
    def executable_alias_is_safe(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("MCP executable aliases must be simple local identifiers")
        return value

    @field_validator("arguments")
    @classmethod
    def arguments_are_bounded(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > MAX_MCP_ARGUMENTS:
            raise ValueError("MCP server commands support at most 64 arguments")
        for value in values:
            if not value or len(value) > MAX_MCP_ARGUMENT_CHARS:
                raise ValueError("MCP server arguments must be 1 to 4096 characters")
            if "\x00" in value or "\r" in value or "\n" in value:
                raise ValueError("MCP server arguments must be single-line and NUL-free")
        return values

    @field_validator("working_directory")
    @classmethod
    def working_directory_is_relative(cls, value: str) -> str:
        if "\x00" in value or Path(value).is_absolute():
            raise ValueError("MCP working directories must be relative and NUL-free")
        return value

    @field_validator("environment")
    @classmethod
    def environment_is_explicit_and_safe(
        cls,
        environment: dict[str, str],
    ) -> dict[str, str]:
        if len(environment) > MAX_MCP_ENVIRONMENT_ENTRIES:
            raise ValueError("MCP environments support at most 32 overrides")
        for name, value in environment.items():
            if not _ENVIRONMENT_NAME_PATTERN.fullmatch(name):
                raise ValueError("MCP environment names must be uppercase identifiers")
            if is_sensitive_environment_key(name):
                raise ValueError("Sensitive MCP environment overrides are not allowed")
            if name not in _SAFE_ENVIRONMENT_OVERRIDES:
                raise ValueError(f"MCP environment override is not allowlisted: {name}")
            if "\x00" in value or len(value) > 4_096:
                raise ValueError("MCP environment values must be bounded and NUL-free")
        return environment

    @field_validator("supported_protocol_versions")
    @classmethod
    def protocol_versions_are_supported(
        cls,
        versions: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not versions:
            raise ValueError("At least one MCP protocol version must be configured")
        if len(versions) != len(set(versions)):
            raise ValueError("MCP protocol versions must be unique")
        unsupported = set(versions) - set(SUPPORTED_MCP_PROTOCOL_VERSIONS)
        if unsupported:
            raise ValueError("Unsupported MCP protocol version configured")
        return versions

    @field_validator("capability_map")
    @classmethod
    def capability_map_is_bounded(
        cls,
        capability_map: dict[str, tuple[ToolCapability, ...]],
    ) -> dict[str, tuple[ToolCapability, ...]]:
        if not capability_map:
            raise ValueError("MCP servers require an explicit tool capability map")
        if len(capability_map) > MAX_MCP_TOOLS:
            raise ValueError("MCP servers support at most 256 configured tools")
        namespaces: set[str] = set()
        for tool_name, capabilities in capability_map.items():
            _validate_external_tool_name(tool_name)
            normalized = _normalize_tool_segment(tool_name)
            if normalized in namespaces:
                raise ValueError("MCP tool names collide after namespace normalization")
            namespaces.add(normalized)
            if not capabilities:
                raise ValueError("Imported MCP tools must declare capabilities")
            if len(capabilities) > 64:
                raise ValueError("Imported MCP tools support at most 64 capabilities")
            fingerprints = [item.model_dump_json() for item in capabilities]
            if len(fingerprints) != len(set(fingerprints)):
                raise ValueError("Imported MCP tool capabilities must be unique")
        return capability_map

    @model_validator(mode="after")
    def transport_and_capabilities_are_constrained(self) -> "McpServerConfig":
        if self.inherit_environment:
            raise ValueError("MCP servers cannot inherit the unrestricted environment")
        if self.transport == McpTransportKind.STDIO:
            if self.executable_alias is None:
                raise ValueError("stdio MCP servers require an executable alias")
            if self.endpoint_url is not None or self.authorization_token is not None:
                raise ValueError("stdio MCP servers cannot configure HTTP credentials")
            if self.explicit_loopback_http:
                raise ValueError("stdio MCP servers cannot enable loopback HTTP")
        else:
            self._validate_loopback_http()

        expected_mcp_scope = CapabilityScope(mcp_servers=(self.server_id,))
        for capabilities in self.capability_map.values():
            names = {capability.name for capability in capabilities}
            required = {
                ToolCapabilityName.MCP_CONNECT,
                ToolCapabilityName.MCP_INVOKE,
            }
            if not required.issubset(names):
                raise ValueError(
                    "Imported MCP tools require mcp.connect and mcp.invoke capabilities"
                )
            for capability in capabilities:
                if (
                    capability.name in required
                    and capability.scope != expected_mcp_scope
                ):
                    raise ValueError(
                        "MCP capabilities must be scoped to the configured server ID"
                    )
        return self

    def capabilities_for(self, tool_name: str) -> tuple[ToolCapability, ...]:
        try:
            return self.capability_map[tool_name]
        except KeyError as exc:
            raise ValueError(
                f"MCP tool has no explicit capability mapping: {tool_name}."
            ) from exc

    def _validate_loopback_http(self) -> None:
        if not self.explicit_loopback_http:
            raise ValueError("loopback HTTP MCP must be explicitly enabled")
        if self.executable_alias is not None or self.arguments or self.environment:
            raise ValueError("HTTP MCP servers cannot configure a local command")
        if self.endpoint_url is None:
            raise ValueError("loopback HTTP MCP requires an endpoint URL")
        parsed = urlsplit(self.endpoint_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("MCP HTTP endpoints must use http or https")
        if parsed.hostname not in {"127.0.0.1", "::1"}:
            raise ValueError(
                "MCP HTTP endpoints require an explicit numeric loopback host"
            )
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("MCP HTTP endpoints cannot contain credentials or URL extras")
        if self.authorization_token is None or len(
            self.authorization_token.get_secret_value()
        ) < 16:
            raise ValueError("loopback HTTP MCP requires a bounded bearer token")


def mcp_server_capabilities(
    server_id: str,
    *additional: ToolCapability,
) -> tuple[ToolCapability, ...]:
    if not _IDENTIFIER_PATTERN.fullmatch(server_id):
        raise ValueError("Invalid MCP server ID")
    if any(
        capability.name
        in {ToolCapabilityName.MCP_CONNECT, ToolCapabilityName.MCP_INVOKE}
        for capability in additional
    ):
        raise ValueError("MCP transport capabilities are added automatically")
    scope = CapabilityScope(mcp_servers=(server_id,))
    return (
        ToolCapability(name=ToolCapabilityName.MCP_CONNECT, scope=scope),
        ToolCapability(name=ToolCapabilityName.MCP_INVOKE, scope=scope),
        *additional,
    )


def namespace_mcp_tool(server_id: str, tool_name: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(server_id):
        raise ValueError("Invalid MCP server ID")
    _validate_external_tool_name(tool_name)
    namespaced = f"mcp.{server_id}.{_normalize_tool_segment(tool_name)}"
    if len(namespaced) > 128:
        raise ValueError("Namespaced MCP tool name exceeds 128 characters")
    return namespaced


def _validate_external_tool_name(tool_name: str) -> None:
    if not _TOOL_NAME_PATTERN.fullmatch(tool_name):
        raise ValueError("MCP tool names must be bounded ASCII identifiers")


def _normalize_tool_segment(tool_name: str) -> str:
    return tool_name.lower()
