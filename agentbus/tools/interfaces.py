from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agentbus.execution.cancellation import CancellationToken
from agentbus.tools.protocol import (
    ToolArtifact,
    ToolDescriptor,
    ToolInvocation,
    ToolResourceUsage,
)


@dataclass(frozen=True)
class ToolExecutionOutput:
    structured_output: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[ToolArtifact, ...] = ()
    exit_code: int | None = None
    resource_usage: ToolResourceUsage = field(default_factory=ToolResourceUsage)
    safe_diagnostic_metadata: dict[str, Any] = field(default_factory=dict)


class ManagedTool(Protocol):
    @property
    def descriptor(self) -> ToolDescriptor: ...

    def execute(
        self,
        invocation: ToolInvocation,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ToolExecutionOutput: ...
