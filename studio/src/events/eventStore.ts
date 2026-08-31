import type { EventEnvelope } from "../api/types";

export const DEFAULT_EVENT_LIMIT = 10_000;

export interface EventStoreMetrics {
  accepted: number;
  batches: number;
  buffered: number;
  cursor: number;
  duplicatesDropped: number;
  evicted: number;
  eventsPerSecond: number;
  invalidDropped: number;
  notifications: number;
  outOfOrderDropped: number;
  size: number;
}

export interface EventStoreSnapshot {
  events: readonly EventEnvelope[];
  metrics: Readonly<EventStoreMetrics>;
  version: number;
}

export interface RunEventSnapshot {
  events: readonly EventEnvelope[];
  latest?: EventEnvelope;
  runId: string;
  version: number;
}

type Listener = () => void;
type ScheduleFlush = (callback: () => void) => void;

const EMPTY_EVENTS: readonly EventEnvelope[] = Object.freeze([]);

export class StudioEventStore {
  private readonly listeners = new Set<Listener>();
  private readonly runListeners = new Map<string, Set<Listener>>();
  private readonly runSnapshots = new Map<string, RunEventSnapshot>();
  private readonly acceptedAt: number[] = [];
  private events: readonly EventEnvelope[] = EMPTY_EVENTS;
  private pending: EventEnvelope[] = [];
  private pendingRuns = new Set<string>();
  private knownSequences = new Set<number>();
  private scheduled = false;
  private cursor = 0;
  private version = 0;
  private accepted = 0;
  private batches = 0;
  private duplicatesDropped = 0;
  private outOfOrderDropped = 0;
  private invalidDropped = 0;
  private evicted = 0;
  private notifications = 0;
  private snapshot: EventStoreSnapshot;

  public constructor(
    private readonly maximumEvents = DEFAULT_EVENT_LIMIT,
    private readonly scheduleFlush: ScheduleFlush = defaultSchedule,
    private readonly now: () => number = monotonicNow
  ) {
    if (!Number.isInteger(maximumEvents) || maximumEvents < 1) {
      throw new Error("Event history limit must be a positive integer.");
    }
    this.snapshot = this.createSnapshot();
  }

  public ingest(event: EventEnvelope): boolean {
    if (!isValidEvent(event)) {
      this.invalidDropped += 1;
      this.requestFlush();
      return false;
    }
    if (event.sequence <= this.cursor) {
      if (this.knownSequences.has(event.sequence)) this.duplicatesDropped += 1;
      else this.outOfOrderDropped += 1;
      this.requestFlush();
      return false;
    }

    this.cursor = event.sequence;
    this.knownSequences.add(event.sequence);
    this.pending.push(event);
    if (event.run_id) this.pendingRuns.add(event.run_id);
    this.accepted += 1;
    this.acceptedAt.push(this.now());
    this.requestFlush();
    return true;
  }

  public ingestBatch(events: readonly EventEnvelope[]): number {
    let accepted = 0;
    for (const event of events) {
      if (this.ingest(event)) accepted += 1;
    }
    return accepted;
  }

  public recordDrop(reason: "duplicate" | "out_of_order" | "invalid"): void {
    if (reason === "duplicate") this.duplicatesDropped += 1;
    else if (reason === "out_of_order") this.outOfOrderDropped += 1;
    else this.invalidDropped += 1;
    this.requestFlush();
  }

  public flush(): void {
    if (!this.scheduled && this.pending.length === 0) return;
    this.scheduled = false;
    const affectedRuns = this.pendingRuns;
    this.pendingRuns = new Set<string>();

    if (this.pending.length > 0) {
      const merged = [...this.events, ...this.pending];
      this.pending = [];
      const overflow = Math.max(0, merged.length - this.maximumEvents);
      if (overflow > 0) {
        this.evicted += overflow;
        this.events = Object.freeze(merged.slice(overflow));
        this.knownSequences = new Set(this.events.map((event) => event.sequence));
        for (const runId of this.runSnapshots.keys()) affectedRuns.add(runId);
      } else {
        this.events = Object.freeze(merged);
      }
    }

    this.trimRateWindow();
    this.batches += 1;
    this.version += 1;
    this.notifications += this.listeners.size;
    for (const runId of affectedRuns) this.notifications += this.runListeners.get(runId)?.size ?? 0;
    this.snapshot = this.createSnapshot();
    this.rebuildRunSnapshots(affectedRuns);
    this.notify(this.listeners);
    for (const runId of affectedRuns) this.notify(this.runListeners.get(runId));
  }

  public clear(): void {
    this.events = EMPTY_EVENTS;
    this.pending = [];
    this.pendingRuns.clear();
    this.knownSequences.clear();
    this.runSnapshots.clear();
    this.acceptedAt.length = 0;
    this.cursor = 0;
    this.accepted = 0;
    this.batches = 0;
    this.duplicatesDropped = 0;
    this.outOfOrderDropped = 0;
    this.invalidDropped = 0;
    this.evicted = 0;
    this.notifications = 0;
    this.version += 1;
    this.snapshot = this.createSnapshot();
    this.notify(this.listeners);
    for (const listeners of this.runListeners.values()) this.notify(listeners);
  }

  public subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  public subscribeRun(runId: string, listener: Listener): () => void {
    const listeners = this.runListeners.get(runId) ?? new Set<Listener>();
    listeners.add(listener);
    this.runListeners.set(runId, listeners);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) this.runListeners.delete(runId);
    };
  }

  public getSnapshot = (): EventStoreSnapshot => this.snapshot;

  public getRunSnapshot(runId: string): RunEventSnapshot {
    return this.runSnapshots.get(runId) ?? {
      events: EMPTY_EVENTS,
      runId,
      version: this.version
    };
  }

  private requestFlush(): void {
    if (this.scheduled) return;
    this.scheduled = true;
    this.scheduleFlush(() => this.flush());
  }

  private createSnapshot(): EventStoreSnapshot {
    this.trimRateWindow();
    return Object.freeze({
      events: this.events,
      metrics: Object.freeze({
        accepted: this.accepted,
        batches: this.batches,
        buffered: this.pending.length,
        cursor: this.cursor,
        duplicatesDropped: this.duplicatesDropped,
        evicted: this.evicted,
        eventsPerSecond: this.acceptedAt.length,
        invalidDropped: this.invalidDropped,
        notifications: this.notifications,
        outOfOrderDropped: this.outOfOrderDropped,
        size: this.events.length
      }),
      version: this.version
    });
  }

  private rebuildRunSnapshots(runIds: Set<string>): void {
    for (const runId of runIds) {
      const events = Object.freeze(this.events.filter((event) => event.run_id === runId));
      this.runSnapshots.set(runId, Object.freeze({
        events,
        latest: events.at(-1),
        runId,
        version: this.version
      }));
    }
  }

  private trimRateWindow(): void {
    const cutoff = this.now() - 1_000;
    let remove = 0;
    while (remove < this.acceptedAt.length && this.acceptedAt[remove] < cutoff) remove += 1;
    if (remove > 0) this.acceptedAt.splice(0, remove);
  }

  private notify(listeners?: Set<Listener>): void {
    if (!listeners) return;
    for (const listener of listeners) listener();
  }
}

function isValidEvent(event: EventEnvelope): boolean {
  return Number.isInteger(event.sequence)
    && event.sequence > 0
    && typeof event.event_type === "string"
    && event.event_type.length > 0
    && typeof event.timestamp === "string";
}

function defaultSchedule(callback: () => void): void {
  if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
    window.requestAnimationFrame(() => callback());
    return;
  }
  queueMicrotask(callback);
}

function monotonicNow(): number {
  return typeof performance === "undefined" ? Date.now() : performance.now();
}
