import assert from "node:assert/strict";
import test from "node:test";
import type { EventEnvelope, RunSummary } from "../generated/protocol";
import { RunStore } from "../runStore";

function run(): RunSummary {
  return {
    run_id: "run-1",
    status: "pending",
    workflow: "multi",
    workspace: "/repo",
    original_task: "Task",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    version: 1
  };
}

function event(sequence: number, eventType: string): EventEnvelope {
  return {
    sequence,
    event_type: eventType,
    timestamp: `2026-01-01T00:00:0${sequence}Z`,
    run_id: "run-1"
  };
}

test("run store rejects duplicate and stale event sequences", () => {
  const store = new RunStore();
  store.replaceRuns([run()]);

  assert.equal(store.apply(event(2, "run_started")), true);
  assert.equal(store.apply(event(2, "run_failed")), false);
  assert.equal(store.apply(event(1, "run_failed")), false);
  assert.equal(store.run("run-1")?.status, "running");
});

test("run store bounds event history and preserves terminal status", () => {
  const store = new RunStore(2);
  store.replaceRuns([run()]);

  store.apply(event(1, "run_succeeded"));
  store.apply(event(2, "run_started"));
  store.apply(event(3, "task_started"));

  assert.equal(store.events().length, 2);
  assert.equal(store.run("run-1")?.status, "succeeded");
});

test("run store reduces cancellation events into lifecycle state", () => {
  const store = new RunStore();
  store.replaceRuns([run()]);

  store.apply({
    ...event(1, "cancellation_requested"),
    payload: { requested_at: "2026-01-01T00:00:01Z", revision: 1 }
  });
  store.apply({
    ...event(2, "provider_cancellation_acknowledged"),
    payload: {
      acknowledged_at: "2026-01-01T00:00:02Z",
      providers: ["deterministic"],
      revision: 2
    }
  });

  assert.equal(store.run("run-1")?.cancellation?.requested, true);
  assert.equal(
    store.run("run-1")?.cancellation?.provider_cancellation_acknowledged,
    true
  );
});
