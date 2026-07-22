from __future__ import annotations

from pathlib import Path

from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityEscalationError,
    ToolCapabilityName,
    ToolDescriptor,
    ToolInvocation,
    ToolResourceUsage,
    capability_fingerprint,
    capability_set_contains,
    validate_tool_arguments,
)


_PATH_ARGUMENTS = ("path", "source", "destination")
_PROCESS_CAPABILITIES = frozenset(
    {
        ToolCapabilityName.PROCESS_EXECUTE,
        ToolCapabilityName.TEST_EXECUTE,
    }
)


def derive_required_capabilities(
    invocation: ToolInvocation,
    descriptor: ToolDescriptor,
) -> tuple[ToolCapability, ...]:
    """Derive least-scope capabilities from arguments, never model claims."""
    validate_tool_arguments(invocation.arguments, descriptor)
    if invocation.tool_name.startswith("mcp."):
        return descriptor.capabilities
    affected_paths = _affected_paths(invocation.arguments)
    executable = invocation.arguments.get("executable")
    working_directory = _working_directory(invocation)
    required: list[ToolCapability] = []
    for declared in descriptor.capabilities:
        updates: dict[str, object] = {}
        if affected_paths:
            updates["affected_paths"] = affected_paths
        if declared.name in _PROCESS_CAPABILITIES:
            if not isinstance(executable, str) or not executable:
                raise ToolCapabilityEscalationError(
                    "Process capability derivation requires an executable alias."
                )
            updates["executables"] = (executable,)
            updates["working_directories"] = (working_directory,)
        scope = CapabilityScope(
            **declared.scope.model_copy(update=updates).model_dump()
        )
        required.append(ToolCapability(name=declared.name, scope=scope))
    result = tuple(required)
    if not capability_set_contains(descriptor.capabilities, result):
        raise ToolCapabilityEscalationError(
            "Derived tool capabilities exceed the descriptor declaration."
        )
    return result


def require_expected_capabilities(
    expected: tuple[ToolCapability, ...],
    required: tuple[ToolCapability, ...],
) -> None:
    if capability_fingerprint(expected) != capability_fingerprint(required):
        raise ToolCapabilityEscalationError(
            "Model-declared capabilities do not exactly match runtime derivation."
        )


def anticipated_tool_usage(invocation: ToolInvocation) -> ToolResourceUsage:
    name = invocation.tool_name
    arguments = invocation.arguments
    if name in {"filesystem.create", "filesystem.write"}:
        content = arguments.get("content")
        written = len(content.encode("utf-8")) if isinstance(content, str) else 0
        return ToolResourceUsage(
            artifact_bytes=written,
            file_mutations=1,
            written_bytes=written,
        )
    if name == "filesystem.patch":
        maximum = invocation.resource_budget.maximum_file_bytes
        return ToolResourceUsage(
            artifact_bytes=maximum,
            file_mutations=1,
            written_bytes=maximum,
        )
    if name == "filesystem.rename":
        return ToolResourceUsage(
            artifact_bytes=invocation.resource_budget.maximum_file_bytes,
            file_mutations=1,
        )
    if name == "filesystem.delete":
        return ToolResourceUsage(file_mutations=1)
    if name in {"git.stage", "git.commit"}:
        paths = arguments.get("paths")
        return ToolResourceUsage(
            file_mutations=len(paths) if isinstance(paths, list) else 0
        )
    return ToolResourceUsage()


def requires_process_slot(invocation: ToolInvocation) -> bool:
    return any(
        capability.name in _PROCESS_CAPABILITIES
        for capability in invocation.requested_capabilities
    )


def _affected_paths(arguments: dict[str, object]) -> tuple[str, ...]:
    paths = [
        value
        for name in _PATH_ARGUMENTS
        if isinstance((value := arguments.get(name)), str)
    ]
    collection = arguments.get("paths")
    if isinstance(collection, list):
        paths.extend(item for item in collection if isinstance(item, str))
    working_directory = arguments.get("working_directory")
    if isinstance(working_directory, str):
        paths.append(working_directory)
    return tuple(dict.fromkeys(paths))


def _working_directory(invocation: ToolInvocation) -> str:
    requested = invocation.arguments.get("working_directory")
    if not isinstance(requested, str):
        return str(Path(invocation.context.worktree_identity).expanduser().resolve())
    candidate = Path(requested).expanduser()
    if not candidate.is_absolute():
        candidate = Path(invocation.context.worktree_identity) / candidate
    return str(candidate.resolve(strict=False))
