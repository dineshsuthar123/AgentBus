from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from agentbus.policy.defaults import ToolPolicyConfiguration
from agentbus.tools.protocol import (
    ToolCapabilityName,
    ToolDescriptor,
    ToolInvocation,
)


_MUTATING_CAPABILITIES = frozenset(
    {
        ToolCapabilityName.FILESYSTEM_WRITE,
        ToolCapabilityName.FILESYSTEM_CREATE,
        ToolCapabilityName.FILESYSTEM_DELETE,
        ToolCapabilityName.FILESYSTEM_RENAME,
        ToolCapabilityName.GIT_WRITE,
        ToolCapabilityName.GIT_COMMIT,
        ToolCapabilityName.GIT_BRANCH,
        ToolCapabilityName.GIT_WORKTREE,
        ToolCapabilityName.PACKAGE_INSTALL,
    }
)
_PROCESS_CAPABILITIES = frozenset(
    {
        ToolCapabilityName.PROCESS_EXECUTE,
        ToolCapabilityName.PROCESS_NETWORK,
        ToolCapabilityName.TEST_EXECUTE,
        ToolCapabilityName.MCP_CONNECT,
        ToolCapabilityName.MCP_INVOKE,
    }
)
_READ_ONLY_CAPABILITIES = frozenset(
    {
        ToolCapabilityName.FILESYSTEM_READ,
        ToolCapabilityName.GIT_READ,
        ToolCapabilityName.ENVIRONMENT_READ_SAFE,
    }
)
_SHELL_EXECUTABLES = frozenset(
    {"bash", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "sh", "zsh"}
)
_DESTRUCTIVE_GIT_ARGUMENTS = frozenset(
    {
        "--delete",
        "--force",
        "--force-with-lease",
        "--global",
        "--hard",
        "clean",
        "config",
        "push",
        "remote",
        "reset",
    }
)
_WINDOWS_DEVICE_PATTERN = re.compile(
    r"(?i)^(?:\\\\[.?]\\|(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$))"
)


@dataclass(frozen=True)
class PolicyFacts:
    descriptor: ToolDescriptor
    invocation: ToolInvocation
    capability_names: frozenset[ToolCapabilityName]
    affected_paths: tuple[str, ...]
    executable: str | None
    arguments: tuple[str, ...]
    working_directory: str
    network_destinations: tuple[str, ...]

    @property
    def mutating(self) -> bool:
        return bool(self.capability_names & _MUTATING_CAPABILITIES)

    @property
    def executes_processes(self) -> bool:
        return bool(self.capability_names & _PROCESS_CAPABILITIES)

    @property
    def read_only(self) -> bool:
        return bool(self.capability_names) and self.capability_names <= _READ_ONLY_CAPABILITIES

    @property
    def requests_network(self) -> bool:
        return (
            ToolCapabilityName.PROCESS_NETWORK in self.capability_names
            or bool(self.network_destinations)
        )


def derive_policy_facts(
    descriptor: ToolDescriptor,
    invocation: ToolInvocation,
) -> PolicyFacts:
    arguments = invocation.arguments
    return PolicyFacts(
        descriptor=descriptor,
        invocation=invocation,
        capability_names=frozenset(
            capability.name for capability in invocation.requested_capabilities
        ),
        affected_paths=_affected_paths(invocation),
        executable=_executable(arguments.get("executable")),
        arguments=_arguments(arguments.get("arguments")),
        working_directory=str(
            arguments.get("working_directory") or invocation.context.worktree_identity
        ),
        network_destinations=_network_destinations(invocation),
    )


def path_is_protected(path: str, configuration: ToolPolicyConfiguration) -> bool:
    normalized = _normalized_relative(path)
    name = PurePosixPath(normalized).name.lower()
    return name in {item.lower() for item in configuration.protected_file_names} or any(
        name.endswith(suffix.lower()) for suffix in configuration.protected_suffixes
    )


def path_requires_approval(
    path: str,
    configuration: ToolPolicyConfiguration,
) -> bool:
    normalized = _normalized_relative(path).lower()
    return any(
        normalized == prefix.rstrip("/").lower()
        or normalized.startswith(prefix.lower())
        for prefix in configuration.approval_path_prefixes
    )


def path_has_unsafe_syntax(path: str) -> bool:
    raw = str(path)
    if not raw or "\x00" in raw:
        return True
    if raw.startswith(("//", "\\\\")) or _WINDOWS_DEVICE_PATTERN.match(raw):
        return True
    normalized = raw.replace("\\", "/")
    if any(part == ".." for part in PurePosixPath(normalized).parts):
        return True
    if _has_windows_alternate_data_stream(raw):
        return True
    return False


def path_is_within_assigned_roots(path: str, invocation: ToolInvocation) -> bool:
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        return not path_has_unsafe_syntax(path)
    target = raw.resolve(strict=False)
    roots = {
        Path(invocation.context.workspace_identity).expanduser().resolve(strict=False),
        Path(invocation.context.worktree_identity).expanduser().resolve(strict=False),
    }
    return any(_is_relative_to(target, root) for root in roots)


def executable_is_standard(
    executable: str | None,
    configuration: ToolPolicyConfiguration,
) -> bool:
    if executable is None:
        return False
    name = Path(executable).name.lower()
    stem = name[:-4] if name.endswith((".exe", ".cmd", ".bat")) else name
    return stem in {item.lower() for item in configuration.standard_executables}


def executable_is_shell(executable: str | None) -> bool:
    return executable is not None and Path(executable).name.lower() in _SHELL_EXECUTABLES


def git_arguments_are_destructive(facts: PolicyFacts) -> bool:
    if not facts.descriptor.name.startswith("git."):
        return False
    normalized = {argument.lower() for argument in facts.arguments}
    return bool(normalized & _DESTRUCTIVE_GIT_ARGUMENTS)


def _affected_paths(invocation: ToolInvocation) -> tuple[str, ...]:
    values: list[str] = []
    arguments = invocation.arguments
    for name in ("path", "source", "destination", "working_directory"):
        value = arguments.get(name)
        if isinstance(value, str):
            values.append(value)
    paths = arguments.get("paths")
    if isinstance(paths, list):
        values.extend(str(path) for path in paths if isinstance(path, str))
    for capability in invocation.requested_capabilities:
        values.extend(capability.scope.affected_paths)
    return tuple(dict.fromkeys(values))


def _network_destinations(invocation: ToolInvocation) -> tuple[str, ...]:
    values: list[str] = []
    destination = invocation.arguments.get("network_destination")
    if isinstance(destination, str):
        values.append(destination)
    for capability in invocation.requested_capabilities:
        values.extend(capability.scope.network_destinations)
    return tuple(dict.fromkeys(values))


def _executable(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _arguments(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _normalized_relative(path: str) -> str:
    value = str(path).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.lstrip("/")


def _has_windows_alternate_data_stream(path: str) -> bool:
    value = path.replace("/", "\\")
    drive, tail = os.path.splitdrive(value)
    return ":" in (tail if drive else value)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
