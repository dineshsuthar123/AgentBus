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


_LAZY_ADAPTER_EXPORTS = frozenset(
    {
        "FileSystemManagedTool",
        "GitManagedTool",
        "ManagedToolContextError",
        "ProcessManagedTool",
        "RepositoryScanManagedTool",
        "builtin_tool_registry",
    }
)


def __getattr__(name: str):
    if name in _LAZY_ADAPTER_EXPORTS:
        return getattr(import_module("agentbus.tools.adapters"), name)
    raise AttributeError(f"module 'agentbus.tools' has no attribute {name!r}")

__all__ = [
    "BUILTIN_TOOL_VERSION",
    "DuplicateToolError",
    "FileSystemManagedTool",
    "GitManagedTool",
    "ManagedTool",
    "ManagedToolContextError",
    "ProcessManagedTool",
    "RepositoryScanManagedTool",
    "ToolExecutionOutput",
    "ToolNotFoundError",
    "ToolOutputCallback",
    "ToolRegistry",
    "ToolRegistryError",
    "ToolVersionMismatchError",
    "anticipated_tool_usage",
    "builtin_descriptors",
    "builtin_tool_registry",
    "derive_required_capabilities",
    "require_expected_capabilities",
    "requires_process_slot",
]
