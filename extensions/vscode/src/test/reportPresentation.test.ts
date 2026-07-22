import assert from "node:assert/strict";
import test from "node:test";
import type { RunReportResponse } from "../generated/protocol";
import { formatReport } from "../reportPresentation";

test("run report includes bounded tool runtime diagnostics", () => {
  const response: RunReportResponse = {
    run_id: "run-1",
    status: "failed",
    report: {
      workspace: "C:/workspace",
      graph_progress: { succeeded: 1, failed: 1 },
      changed_files: ["src/module.py"],
      tool_runtime: {
        invocation_count: 3,
        status_counts: { succeeded: 2, denied: 1 },
        tool_versions: [{ tool_name: "filesystem.write", versions: ["1.0.0"] }],
        policy_decisions: { outcomes: { allow: 2, deny: 1 } },
        approvals: { count: 1, states: { approved: 1 } },
        denied_operations: [{ tool_name: "filesystem.read" }],
        resource_consumption: { wall_clock_seconds: 1.5 },
        output_truncation_count: 1,
        timeout_count: 0,
        cancellation_count: 0,
        mcp_usage: { servers: ["fixture"], invocation_count: 1 },
        artifacts: [{ relative_path: "src/module.py" }],
        security_constraints: ["Raw output bodies are not persisted."]
      }
    }
  };
  const rendered = formatReport(response);

  assert.match(rendered, /## Tool Runtime/);
  for (const field of [
    "invocation_count",
    "tool_versions",
    "policy_decisions",
    "approvals",
    "denied_operations",
    "resource_consumption",
    "output_truncation_count",
    "mcp_usage",
    "artifacts",
    "security_constraints"
  ]) {
    assert.match(rendered, new RegExp(field));
  }
});

test("run report redacts values and cannot escape indented JSON", () => {
  const rendered = formatReport({
    run_id: "[run](command:evil)",
    status: "failed",
    report: {
      failure_reason: "Bearer private-token",
      reviewer_summary: "```\n[click](command:evil)",
      tool_runtime: { diagnostic: "api_key=private-value" }
    }
  });

  assert.doesNotMatch(rendered, /private-token|private-value/);
  assert.doesNotMatch(rendered, /^```/m);
  assert.doesNotMatch(rendered, /\[run\]\(command:evil\)/);
  assert.match(rendered, /\\\[run\\\]/);
  assert.match(rendered, /api_key=\[REDACTED\]/);
});
