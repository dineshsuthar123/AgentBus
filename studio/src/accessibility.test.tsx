import axe from "axe-core";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ApprovalSummary } from "./api/types";
import { App } from "./App";
import { ApprovalGate } from "./components/ApprovalGate";
import { StudioProvider } from "./state/StudioContext";
import { ExecutionMesh } from "./observatory/ExecutionMesh";
import type { ExecutionMeshModel } from "./observatory/model";

const approval: ApprovalSummary = {
  approval_id: "approval-a11y-001",
  run_id: "run-a11y-001",
  task_id: "task-a11y-001",
  risk_category: "process_execution",
  requested_action: "Run bounded repository tests",
  command: ["mvn", "-q", "-o", "test"],
  executable: "mvn",
  created_at: "2026-08-31T10:00:00Z",
  state: "pending",
  revision: 3,
  tool_name: "test.execute"
};

const mesh: ExecutionMeshModel = {
  activeNodeId: "approval",
  width: 760,
  height: 240,
  nodes: [{
    id: "planner", kind: "planner", label: "Planner", detail: "One durable task",
    rawStatus: "succeeded", tone: "success", x: 20, y: 40, width: 142, height: 62
  }, {
    id: "approval", kind: "approval", label: "Approval gate", detail: "Run repository tests",
    rawStatus: "pending", tone: "approval", x: 230, y: 40, width: 142, height: 62,
    approvalId: approval.approval_id
  }],
  edges: [{
    id: "planner->approval", source: "planner", target: "approval", detail: "Exact policy gate",
    tone: "approval", critical: true
  }]
};

describe("Studio accessibility", () => {
  it("passes axe structural checks on the authenticated connection surface", async () => {
    const { container } = render(<StudioProvider><App /></StudioProvider>);
    await expectAxeClean(container);
  });

  it("passes axe structural checks for the approval gate and custom execution graph", async () => {
    const { container } = render(<main><h1>Execution observatory</h1><ApprovalGate approval={approval} onDecision={vi.fn(async () => undefined)} /><ExecutionMesh model={mesh} onSelectNode={vi.fn()} /></main>);
    await expectAxeClean(container);
  });
});

async function expectAxeClean(container: HTMLElement) {
  const result = await axe.run(container, {
    rules: {
      // jsdom does not provide layout/computed paint data; browser screenshots cover contrast.
      "color-contrast": { enabled: false }
    }
  });
  expect(result.violations, result.violations.map((violation) => `${violation.id}: ${violation.help}`).join("\n")).toEqual([]);
}
