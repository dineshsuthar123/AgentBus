from importlib import import_module

from agentbus.tools.descriptors import BUILTIN_TOOL_VERSION, builtin_descriptors
from agentbus.tools.capabilities import (
    anticipated_tool_usage,
    derive_required_capabilities,
    require_expected_capabilities,
    requires_process_slot,
)
from agentbus.tools.interfaces import (
    ManagedTool,
    ToolExecutionOutput,
    ToolOutputCallback,
)
from agentbus.tools.registry import (
    DuplicateToolError,
    ToolNotFoundError,
    ToolRegistry,
    ToolRegistryError,
    ToolVersionMismatchError,
)


_LAZY_EXPORTS = frozenset(
    {
        "FileSystemManagedTool",
        "GitManagedTool",
        "ManagedToolContextError",
        "ProcessManagedTool",
        "RepositoryScanManagedTool",
        "builtin_tool_registry",
        "ToolDispatchResponse",
        "ToolDispatcher",
        "ManagedToolRuntime",
        "build_managed_tool_runtime",
    }
)


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        module_name = (
            "agentbus.tools.dispatcher"
            if name in {"ToolDispatchResponse", "ToolDispatcher"}
            else (
                "agentbus.tools.runtime"
                if name in {"ManagedToolRuntime", "build_managed_tool_runtime"}
                else "agentbus.tools.adapters"
            )
        )
        return getattr(import_module(module_name), name)
    raise AttributeError(f"module 'agentbus.tools' has no attribute {name!r}")

__all__ = [
    "BUILTIN_TOOL_VERSION",
    "DuplicateToolError",
    "FileSystemManagedTool",
    "GitManagedTool",
    "ManagedTool",
    "ManagedToolRuntime",
    "ManagedToolContextError",
    "ProcessManagedTool",
    "RepositoryScanManagedTool",
    "ToolExecutionOutput",
    "ToolDispatchResponse",
    "ToolDispatcher",
    "ToolNotFoundError",
    "ToolOutputCallback",
    "ToolRegistry",
    "ToolRegistryError",
    "ToolVersionMismatchError",
    "anticipated_tool_usage",
    "builtin_descriptors",
    "builtin_tool_registry",
    "build_managed_tool_runtime",
    "derive_required_capabilities",
    "require_expected_capabilities",
    "requires_process_slot",
]
