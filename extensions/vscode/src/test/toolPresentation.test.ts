import assert from "node:assert/strict";
import test from "node:test";
import type { ToolInvocationSummary } from "../generated/protocol";
import {
  TOOL_GROUPS,
  canCancelTool,
  capabilityNames,
  escapeMarkdown,
  toolDuration,
  toolGroup,
  toolResourceSummary,
  toolVersion
} from "../toolPresentation";

function invocation(
  status: string,
  overrides: Partial<ToolInvocationSummary> = {}
): ToolInvocationSummary {
  return {
    invocation_sequence: 1,
    invocation_id: "invoke-1",
    invocation_revision: 1,
    run_id: "run-1",
    task_id: "task-1",
    tool_name: "filesystem.read",
    tool_version: { major: 1, minor: 2, patch: 3 },
    protocol_version: "1.0",
    caller_role: "coder",
    status,
    capabilities: [
      { name: "filesystem.read", scope: { roots: ["C:/workspace"] } }
    ],
    resource_budget: {},
    resource_usage: {},
    cancellation: {},
    requested_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:01Z",
    ...overrides
  };
}

test("tool statuses map to all required lifecycle groups", () => {
  assert.deepEqual(
    TOOL_GROUPS.map((group) => group.key),
    [
      "active",
      "awaiting_approval",
      "succeeded",
      "failed",
      "denied",
      "cancelled",
      "timed_out"
    ]
  );
  assert.equal(toolGroup("requested"), "active");
  assert.equal(toolGroup("running"), "active");
  for (const status of TOOL_GROUPS.slice(1).map((group) => group.key)) {
    assert.equal(toolGroup(status), status);
  }
});

test("tool presentation reports capabilities duration resources and version", () => {
  const value = invocation("succeeded", {
    resource_usage: {
      wall_clock_seconds: 1.25,
      stdout_bytes: 10,
      stderr_bytes: 2,
      file_mutations: 1,
      child_processes: 0
    }
  });

  assert.equal(capabilityNames(value.capabilities), "filesystem.read");
  assert.equal(toolVersion(value), "1.2.3");
  assert.equal(toolDuration(value), "1.25s");
  assert.match(toolResourceSummary(value), /12 B output/);
  assert.equal(canCancelTool("running"), true);
  assert.equal(canCancelTool("awaiting_approval"), true);
  assert.equal(canCancelTool("succeeded"), false);
});

test("tool tooltip values escape Markdown control characters", () => {
  assert.equal(
    escapeMarkdown("[tool](command:evil) `unsafe`"),
    "\\[tool\\]\\(command:evil\\) \\`unsafe\\`"
  );
});
