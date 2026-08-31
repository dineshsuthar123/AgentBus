import type { RunBundle } from "../api/runBundle";
import { describe, expect, it } from "vitest";
import { buildTimeline } from "./timelineModel";

const bundle = {
  run: {
    run_id: "run-1", status: "running", workflow: "multi", workspace: "C:\\repo",
    original_task: "Repair retry", created_at: "2026-08-31T10:00:00Z",
    updated_at: "2026-08-31T10:00:02Z", version: 1
  },
  tasks: { run_id: "run-1", tasks: [] },
  approvals: {
    run_id: "run-1",
    approvals: [{
      approval_id: "approval-1", run_id: "run-1", task_id: "step-1",
      risk_category: "process_execution", requested_action: "Run tests",
      created_at: "2026-08-31T10:00:02Z", state: "pending"
    }]
  },
  spans: {
    trace_id: "trace-1", run_id: "run-1",
    spans: [{
      trace_id: "trace-1", span_id: "span-1", run_id: "run-1",
      span_type: "planner", name: "Plan durable graph", sequence: 1,
      started_at: "2026-08-31T10:00:00Z", status: "succeeded"
    }]
  }
} satisfies RunBundle;

describe("timeline model", () => {
  it("uses redacted monotonic event envelopes when they are available", () => {
    const records = buildTimeline(bundle, [{
      sequence: 44,
      event_type: "tool.awaiting_approval",
      timestamp: "2026-08-31T10:00:02Z",
      run_id: "run-1",
      task_id: "step-1",
      payload: { status: "awaiting_approval", tool_name: "test.execute" }
    }]);

    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({
      id: "event:44",
      source: "event",
      sequence: 44,
      status: "awaiting_approval",
      summary: "test.execute"
    });
  });

  it("falls back only to persisted span and approval records", () => {
    const records = buildTimeline(bundle, []);
    expect(records.map((record) => record.source)).toEqual(["span", "approval"]);
    expect(records[1]).toMatchObject({ nodeId: "approval:approval-1", title: "Run tests" });
  });
});
