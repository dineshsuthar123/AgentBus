import assert from "node:assert/strict";
import test from "node:test";
import type {
  CancellationLifecycle,
  EventEnvelope,
  RunSummary
} from "../generated/protocol";
import {
  applyCancellationEvent,
  canCancel,
  cancellationDetails,
  cancellationStatus
} from "../cancellation";

function event(
  sequence: number,
  eventType: string,
  payload: Record<string, unknown> = {}
): EventEnvelope {
  return {
    sequence,
    event_type: eventType,
    timestamp: `2026-01-01T00:00:0${sequence}Z`,
    run_id: "run-1",
    payload
  };
}

function run(
  status = "running",
  cancellation?: CancellationLifecycle
): RunSummary {
  return {
    run_id: "run-1",
    status,
    workflow: "multi",
    workspace: "/repo",
    original_task: "Task",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    version: 1,
    cancellation
  };
}

test("cancellation lifecycle reports every user-visible transition", () => {
  let lifecycle = applyCancellationEvent(
    undefined,
    event(1, "cancellation_requested", {
      requested_at: "2026-01-01T00:00:01Z",
      revision: 1
    })
  );
  assert.equal(cancellationStatus(lifecycle), "Cancelling...");

  lifecycle = applyCancellationEvent(
    lifecycle,
    event(2, "provider_cancellation_requested", {
      requested_at: "2026-01-01T00:00:02Z",
      providers: ["deterministic"],
      revision: 2
    })
  );
  assert.ok(
    cancellationDetails(lifecycle).includes("Provider cancellation signalled")
  );

  lifecycle = applyCancellationEvent(
    lifecycle,
    event(3, "provider_cancellation_acknowledged", {
      acknowledged_at: "2026-01-01T00:00:03Z",
      source: "provider:deterministic",
      providers: ["deterministic"],
      revision: 3
    })
  );
  assert.equal(cancellationStatus(lifecycle), "Cancellation acknowledged");

  lifecycle = {
    ...lifecycle,
    active_non_interruptible_operations: ["verifier.command"]
  };
  assert.equal(
    cancellationStatus(lifecycle),
    "Waiting for active provider operation"
  );

  lifecycle = applyCancellationEvent(
    lifecycle,
    event(4, "operation_completed_after_cancellation", {
      operation: "verifier.command",
      revision: 4
    })
  );
  assert.ok(
    cancellationDetails(lifecycle).includes(
      "Completed after cancellation request"
    )
  );

  lifecycle = applyCancellationEvent(
    lifecycle,
    event(5, "scheduling_stopped", {
      stopped_at: "2026-01-01T00:00:05Z",
      tasks_prevented_from_starting: ["step-2"],
      revision: 5
    })
  );
  lifecycle = { ...lifecycle, active_non_interruptible_operations: [] };
  assert.equal(cancellationStatus(lifecycle), "Scheduling stopped");

  lifecycle = applyCancellationEvent(
    lifecycle,
    event(6, "cancellation_cleanup_completed", {
      completed_at: "2026-01-01T00:00:06Z",
      resume_eligible: false,
      tasks_completed_after_request: ["step-1"],
      revision: 6
    })
  );
  assert.equal(cancellationStatus(lifecycle, "cancelled"), "Cancelled");
  assert.ok(
    cancellationDetails(lifecycle, "cancelled").includes("Resume unavailable")
  );
});

test("cancel action is disabled after request acknowledgement or terminal state", () => {
  assert.equal(canCancel(run()), true);
  assert.equal(canCancel(run("running", { requested: true })), false);
  assert.equal(canCancel(run("running", { acknowledged: true })), false);
  assert.equal(canCancel(run("cancelled")), false);
});

test("stale cancellation events cannot replace newer lifecycle state", () => {
  const current: CancellationLifecycle = {
    requested: true,
    acknowledged: true,
    revision: 5
  };

  assert.equal(
    applyCancellationEvent(
      current,
      event(2, "cancellation_requested", { revision: 2 })
    ),
    current
  );
});
