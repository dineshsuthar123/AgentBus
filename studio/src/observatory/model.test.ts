import type { RunBundle } from "../api/runBundle";
import { describe, expect, it } from "vitest";
import { buildExecutionMesh, projectExecutionMesh } from "./model";

function bundle(): RunBundle {
  return {
    run: {
      run_id: "run-1",
      status: "succeeded",
      workflow: "multi",
      workspace: "C:\\repo",
      original_task: "Repair payment retries",
      created_at: "2026-08-31T10:00:00Z",
      updated_at: "2026-08-31T10:05:00Z",
      reviewer_status: "approved",
      version: 4
    },
    tasks: {
      run_id: "run-1",
      tasks: [{
        task_id: "step-1",
        title: "Make confirmation atomic",
        description: "Replace the race",
        status: "succeeded",
        position: 0,
        assigned_role: "coder",
        risk: "medium",
        attempts: 2,
        verifier_status: "passed",
        reviewer_status: "approved",
        created_at: "2026-08-31T10:00:01Z",
        updated_at: "2026-08-31T10:04:00Z"
      }]
    },
    attempts: {
      run_id: "run-1",
      total: 2,
      attempts: [{
        attempt_id: "attempt-1",
        task_id: "step-1",
        attempt_number: 1,
        status: "failed",
        started_at: "2026-08-31T10:01:00Z",
        completed_at: "2026-08-31T10:02:00Z",
        verifier_status: "failed",
        retry_evidence: {
          source_attempt_id: "attempt-1",
          source_attempt_number: 1,
          failure_category: "test_failure",
          candidate_identity_sha256: "a".repeat(64),
          diagnostics: { kind: "test", summary: "Concurrent retry test failed." },
          created_at: "2026-08-31T10:02:00Z",
          evidence_sha256: "b".repeat(64)
        }
      }, {
        attempt_id: "attempt-2",
        task_id: "step-1",
        attempt_number: 2,
        status: "succeeded",
        started_at: "2026-08-31T10:02:01Z",
        completed_at: "2026-08-31T10:04:00Z",
        verifier_status: "passed"
      }]
    },
    approvals: { run_id: "run-1", approvals: [] },
    trace: {
      trace_id: "trace-1",
      run_id: "run-1",
      root_span_id: "span-1",
      schema_version: 1,
      status: "sealed",
      created_at: "2026-08-31T10:00:00Z",
      completed_at: "2026-08-31T10:05:00Z",
      span_count: 8,
      event_count: 24,
      checkpoint_count: 2
    }
  };
}

describe("execution mesh model", () => {
  it("branches retries only from persisted attempts and RetryEvidence", () => {
    const model = buildExecutionMesh(bundle());

    expect(model.nodes.map((node) => node.id)).toEqual(expect.arrayContaining([
      "planner",
      "coder:step-1:1",
      "verifier:step-1:1",
      "retry:attempt-1",
      "coder:step-1:2",
      "verifier:step-1:2",
      "final-reviewer",
      "evidence"
    ]));
    expect(model.edges).toContainEqual(expect.objectContaining({
      source: "retry:attempt-1",
      target: "coder:step-1:2",
      tone: "replay"
    }));
  });

  it("places a pending exact approval in the path and dims downstream gates", () => {
    const waiting = bundle();
    waiting.run.status = "waiting_for_approval";
    waiting.run.reviewer_status = "not_run";
    waiting.trace!.status = "running";
    waiting.approvals.approvals = [{
      approval_id: "approval-1",
      run_id: "run-1",
      task_id: "step-1",
      risk_category: "process_execution",
      reason: "Exact Maven execution requires consent.",
      requested_action: "Run Maven tests",
      created_at: "2026-08-31T10:03:00Z",
      state: "pending",
      revision: 3,
      tool_name: "test.execute"
    }];

    const model = buildExecutionMesh(waiting);
    expect(model.activeNodeId).toBe("approval:approval-1");
    expect(model.nodes.find((node) => node.id === "approval:approval-1")).toMatchObject({
      tone: "approval",
      approvalId: "approval-1"
    });
    expect(model.nodes.find((node) => node.id === "evidence")?.dimmed).toBe(true);
    expect(model.nodes.filter((node) => node.kind === "retry")).toHaveLength(1);
  });

  it("projects historical presentation without mutating current execution state", () => {
    const current = buildExecutionMesh(bundle());
    const historical = projectExecutionMesh(current, "2026-08-31T10:01:30Z");

    expect(current.nodes.find((node) => node.id === "evidence")?.tone).toBe("success");
    expect(historical.nodes.find((node) => node.id === "evidence")).toMatchObject({
      tone: "blocked",
      rawStatus: "not observed at cursor",
      dimmed: true
    });
    expect(historical.activeNodeId).toBe("coder:step-1:1");
  });
});
