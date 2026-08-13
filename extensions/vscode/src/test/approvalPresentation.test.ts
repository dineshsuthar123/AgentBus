import assert from "node:assert/strict";
import test from "node:test";
import type { ApprovalSummary } from "../generated/protocol";
import {
  ApprovalStateConflictError,
  formatApprovalConfirmation,
  formatApprovalTooltip,
  reconcileApprovalSummaries
} from "../approvalPresentation";

function toolApproval(): ApprovalSummary {
  return {
    approval_id: "approval-1",
    run_id: "run-1",
    task_id: "task-1",
    risk_category: "tool",
    reason: "CI policy modification",
    requested_action: "Invoke filesystem.write",
    affected_paths: [".github/workflows/ci.yml"],
    created_at: "2026-01-01T00:00:00Z",
    state: "pending",
    revision: 2,
    approval_kind: "tool",
    tool_name: "filesystem.write",
    capabilities: [
      {
        name: "filesystem.write",
        scope: {
          roots: ["C:/workspace"],
          affected_paths: [".github/workflows/ci.yml"]
        }
      }
    ],
    arguments_summary: ["--token=private-value", "ci.yml"],
    executable: "python",
    working_directory: "C:/workspace",
    network_destination: "https://example.invalid/api?token=private",
    policy_rule: "approval.sensitive_path",
    proposed_constraints: [
      {
        name: "filesystem.write",
        scope: { affected_paths: [".github/workflows/ci.yml"] }
      }
    ],
    resource_budget: {
      wall_clock_seconds: 30,
      total_written_bytes: 4096
    }
  };
}

test("tool approval confirmation displays exact bounded security scope", () => {
  const rendered = formatApprovalConfirmation(toolApproval());

  assert.match(rendered, /filesystem\.write/);
  assert.match(rendered, /\.github\/workflows\/ci\.yml/);
  assert.match(rendered, /Executable: python/);
  assert.match(rendered, /Working directory: C:\/workspace/);
  assert.match(rendered, /Policy rule: approval\.sensitive_path/);
  assert.match(rendered, /wall_clock_seconds=30/);
  assert.match(rendered, /token=\[REDACTED\]/);
  assert.doesNotMatch(rendered, /private-value|token=private/);
});

test("approval tooltip escapes Markdown and reports constraints and risk", () => {
  const approval = toolApproval();
  approval.reason = "[risk](command:evil)";
  const rendered = formatApprovalTooltip(approval);

  assert.match(rendered, /Requested capabilities:/);
  assert.match(rendered, /Constraints:/);
  assert.match(rendered, /Risk reason:/);
  assert.doesNotMatch(rendered, /\[risk\]\(command:evil\)/);
  assert.match(rendered, /\\\[risk\\\]/);
});

test("approval reconciliation collapses exact restored duplicates", () => {
  const approval = toolApproval();

  const reconciled = reconcileApprovalSummaries([
    approval,
    structuredClone(approval),
    structuredClone(approval)
  ]);

  assert.deepEqual(reconciled, [approval]);
});

test("approval reconciliation selects a newer revision only for exact scope", () => {
  const pending = toolApproval();
  const approved = {
    ...structuredClone(pending),
    state: "approved",
    revision: 3
  };

  assert.deepEqual(reconcileApprovalSummaries([pending, approved]), [approved]);
});

test("approval reconciliation fails closed on duplicate scope widening", () => {
  const approval = toolApproval();
  const widened = structuredClone(approval);
  widened.affected_paths = [
    ...(widened.affected_paths ?? []),
    "C:/workspace-secondary/**"
  ];

  assert.throws(
    () => reconcileApprovalSummaries([approval, widened]),
    ApprovalStateConflictError
  );
});

test("approval reconciliation rejects conflicting same-revision state", () => {
  const approval = toolApproval();
  const conflicting = { ...structuredClone(approval), state: "approved" };

  assert.throws(
    () => reconcileApprovalSummaries([approval, conflicting]),
    ApprovalStateConflictError
  );
});

test("approval reconciliation rejects a newer duplicate that reopens approval", () => {
  const approved = { ...toolApproval(), state: "approved", revision: 3 };
  const reopened = { ...structuredClone(approved), state: "pending", revision: 4 };

  assert.throws(
    () => reconcileApprovalSummaries([approved, reopened]),
    ApprovalStateConflictError
  );
});

test("approval reconciliation rejects conflicting terminal transitions", () => {
  const approved = { ...toolApproval(), state: "approved", revision: 3 };
  const rejected = { ...structuredClone(approved), state: "rejected", revision: 4 };

  assert.throws(
    () => reconcileApprovalSummaries([approved, rejected]),
    ApprovalStateConflictError
  );
});
