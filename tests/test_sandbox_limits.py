from __future__ import annotations

import pytest

from agentbus.sandbox import (
    ProcessOutputSnapshot,
    effective_wall_clock_limit,
    process_resource_usage,
)
from agentbus.tools.protocol import ToolLimitUsage, ToolResourceBudget


def _output() -> ProcessOutputSnapshot:
    return ProcessOutputSnapshot(
        stdout="out",
        stderr="err",
        stdout_bytes=12,
        stderr_bytes=7,
        retained_stdout_bytes=3,
        retained_stderr_bytes=3,
        stdout_truncated=True,
        stderr_truncated=True,
        output_events=2,
        output_events_truncated=False,
        callback_failures=0,
    )


def test_effective_timeout_cannot_expand_resource_budget() -> None:
    budget = ToolResourceBudget(wall_clock_seconds=5)

    assert effective_wall_clock_limit(None, budget) == 5
    assert effective_wall_clock_limit(2, budget) == 2
    assert effective_wall_clock_limit(20, budget) == 5
    with pytest.raises(ValueError, match="positive"):
        effective_wall_clock_limit(0, budget)


def test_process_usage_reports_enforced_and_unsupported_limits_truthfully() -> None:
    budget = ToolResourceBudget(
        wall_clock_seconds=5,
        stdout_bytes=10,
        stderr_bytes=10,
        combined_output_bytes=20,
        child_processes=2,
        memory_bytes=10_000_000,
        cpu_seconds=3,
    )

    usage = process_resource_usage(
        budget=budget,
        duration_seconds=1.25,
        output=_output(),
    )

    assert usage.wall_clock_seconds == 1.25
    assert usage.stdout_bytes == 12
    assert usage.stderr_bytes == 7
    assert usage.limits["stdout_bytes"].enforced is True
    assert usage.limits["combined_output_bytes"].observed == 19
    assert usage.limits["child_processes"].supported is False
    assert usage.limits["memory_bytes"].enforced is False
    assert usage.limits["cpu_seconds"].observed is None


def test_platform_backend_can_supply_only_process_limit_observations() -> None:
    child_limit = ToolLimitUsage(
        requested=2,
        supported=True,
        enforced=True,
        observed=1,
        diagnostic="Enforced by test process container.",
    )
    usage = process_resource_usage(
        budget=ToolResourceBudget(child_processes=2),
        duration_seconds=0.1,
        output=_output(),
        child_processes=1,
        platform_limits={"child_processes": child_limit},
    )

    assert usage.child_processes == 1
    assert usage.limits["child_processes"] == child_limit
    with pytest.raises(ValueError, match="Unsupported"):
        process_resource_usage(
            budget=ToolResourceBudget(),
            duration_seconds=0.1,
            output=_output(),
            platform_limits={"network": child_limit},
        )
    with pytest.raises(ValueError, match="does not match"):
        process_resource_usage(
            budget=ToolResourceBudget(child_processes=3),
            duration_seconds=0.1,
            output=_output(),
            platform_limits={"child_processes": child_limit},
        )
