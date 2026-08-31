import type { EventEnvelope } from "../api/types";
import { describe, expect, it, vi } from "vitest";
import { StudioEventStore } from "./eventStore";

function event(sequence: number, runId = "run-a"): EventEnvelope {
  return {
    sequence,
    event_type: "task.updated",
    timestamp: "2026-08-31T10:00:00Z",
    run_id: runId,
    task_id: `task-${sequence}`,
    payload: { status: "running" }
  };
}

describe("StudioEventStore", () => {
  it("batches a burst into one notification and scopes run subscribers", () => {
    const scheduled: Array<() => void> = [];
    const store = new StudioEventStore(100, (callback) => scheduled.push(callback));
    const globalListener = vi.fn();
    const runAListener = vi.fn();
    const runBListener = vi.fn();
    store.subscribe(globalListener);
    store.subscribeRun("run-a", runAListener);
    store.subscribeRun("run-b", runBListener);

    store.ingestBatch([event(1), event(2), event(3)]);
    expect(scheduled).toHaveLength(1);
    expect(globalListener).not.toHaveBeenCalled();

    scheduled[0]();
    expect(globalListener).toHaveBeenCalledOnce();
    expect(runAListener).toHaveBeenCalledOnce();
    expect(runBListener).not.toHaveBeenCalled();
    expect(store.getRunSnapshot("run-a").events).toHaveLength(3);
    expect(store.getSnapshot().metrics).toMatchObject({ batches: 1, notifications: 2 });
  });

  it("drops duplicate and out-of-order events before presentation", () => {
    const store = new StudioEventStore(10, () => undefined);
    expect(store.ingest(event(4))).toBe(true);
    store.flush();
    expect(store.ingest(event(4))).toBe(false);
    expect(store.ingest(event(2))).toBe(false);
    store.flush();

    expect(store.getSnapshot().events.map((item) => item.sequence)).toEqual([4]);
    expect(store.getSnapshot().metrics.duplicatesDropped).toBe(1);
    expect(store.getSnapshot().metrics.outOfOrderDropped).toBe(1);
  });

  it("bounds retained history while preserving the monotonic cursor", () => {
    const store = new StudioEventStore(3, () => undefined);
    store.ingestBatch([event(1), event(2), event(3), event(4), event(5)]);
    store.flush();

    expect(store.getSnapshot().events.map((item) => item.sequence)).toEqual([3, 4, 5]);
    expect(store.getSnapshot().metrics).toMatchObject({ cursor: 5, evicted: 2, size: 3 });
    expect(store.ingest(event(1))).toBe(false);
    store.flush();
    expect(store.getSnapshot().metrics.outOfOrderDropped).toBe(1);
  });

  it("ingests ten thousand deterministic events in one render batch", () => {
    const scheduled: Array<() => void> = [];
    const store = new StudioEventStore(10_000, (callback) => scheduled.push(callback));
    const listener = vi.fn();
    store.subscribe(listener);
    const fixture = Array.from({ length: 10_000 }, (_, index) => event(index + 1));

    expect(store.ingestBatch(fixture)).toBe(10_000);
    expect(scheduled).toHaveLength(1);
    scheduled[0]();

    expect(store.getSnapshot().metrics).toMatchObject({
      accepted: 10_000,
      batches: 1,
      buffered: 0,
      cursor: 10_000,
      size: 10_000
    });
    expect(listener).toHaveBeenCalledOnce();
  });
});
