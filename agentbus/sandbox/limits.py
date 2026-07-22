from __future__ import annotations

from collections.abc import Mapping

from agentbus.sandbox.output import ProcessOutputSnapshot
from agentbus.tools.protocol import (
    ToolLimitUsage,
    ToolResourceBudget,
    ToolResourceUsage,
)


def effective_wall_clock_limit(
    requested_timeout_seconds: float | None,
    budget: ToolResourceBudget,
) -> float:
    if requested_timeout_seconds is None:
        return budget.wall_clock_seconds
    if requested_timeout_seconds <= 0:
        raise ValueError("Process timeout must be positive.")
    return min(float(requested_timeout_seconds), budget.wall_clock_seconds)


def process_resource_usage(
    *,
    budget: ToolResourceBudget,
    duration_seconds: float,
    output: ProcessOutputSnapshot,
    child_processes: int = 0,
    memory_bytes: int | None = None,
    cpu_seconds: float | None = None,
    platform_limits: Mapping[str, ToolLimitUsage] | None = None,
) -> ToolResourceUsage:
    observed_combined = output.stdout_bytes + output.stderr_bytes
    limits: dict[str, ToolLimitUsage] = {
        "wall_clock_seconds": ToolLimitUsage(
            requested=budget.wall_clock_seconds,
            supported=True,
            enforced=True,
            observed=duration_seconds,
            diagnostic="Supervisor timeout with bounded process-tree escalation.",
        ),
        "stdout_bytes": ToolLimitUsage(
            requested=budget.stdout_bytes,
            supported=True,
            enforced=True,
            observed=output.stdout_bytes,
            diagnostic="Captured bytes are bounded while excess output is drained.",
        ),
        "stderr_bytes": ToolLimitUsage(
            requested=budget.stderr_bytes,
            supported=True,
            enforced=True,
            observed=output.stderr_bytes,
            diagnostic="Captured bytes are bounded while excess output is drained.",
        ),
        "combined_output_bytes": ToolLimitUsage(
            requested=budget.combined_output_bytes,
            supported=True,
            enforced=True,
            observed=observed_combined,
            diagnostic="Combined retained output is bounded across both streams.",
        ),
        "child_processes": _unsupported_limit(
            budget.child_processes,
            "Descendant counting requires a platform process container.",
        ),
        "memory_bytes": _unsupported_limit(
            budget.memory_bytes,
            "Memory enforcement is unavailable in the active process backend.",
        ),
        "cpu_seconds": _unsupported_limit(
            budget.cpu_seconds,
            "CPU-time enforcement is unavailable in the active process backend.",
        ),
    }
    requested_limits: dict[str, int | float | None] = {
        "child_processes": budget.child_processes,
        "memory_bytes": budget.memory_bytes,
        "cpu_seconds": budget.cpu_seconds,
    }
    for name, observation in (platform_limits or {}).items():
        if name not in {"child_processes", "memory_bytes", "cpu_seconds"}:
            raise ValueError(f"Unsupported platform process limit override: {name}.")
        if observation.requested != requested_limits[name]:
            raise ValueError(
                f"Platform process limit does not match the requested budget: {name}."
            )
        limits[name] = observation
    return ToolResourceUsage(
        wall_clock_seconds=max(0.0, duration_seconds),
        stdout_bytes=output.stdout_bytes,
        stderr_bytes=output.stderr_bytes,
        child_processes=max(0, child_processes),
        memory_bytes=memory_bytes,
        cpu_seconds=cpu_seconds,
        limits=limits,
    )


def _unsupported_limit(
    requested: int | float | None,
    diagnostic: str,
) -> ToolLimitUsage:
    return ToolLimitUsage(
        requested=requested,
        supported=False,
        enforced=False,
        observed=None,
        diagnostic=diagnostic,
    )
