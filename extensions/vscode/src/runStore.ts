import type { EventEnvelope, RunSummary } from "./generated/protocol";
import { applyCancellationEvent } from "./cancellation";

const terminalStatuses = new Set(["succeeded", "failed", "cancelled"]);

export class RunStore {
  private readonly runsById = new Map<string, RunSummary>();
  private readonly history: EventEnvelope[] = [];
  private readonly listeners = new Set<() => void>();
  private highestSequence = 0;

  public constructor(private readonly maximumEvents = 500) {
    if (maximumEvents < 1) {
      throw new Error("maximumEvents must be positive.");
    }
  }

  public replaceRuns(runs: RunSummary[]): void {
    const preserved = [...this.runsById.values()].filter(
      (run) =>
        !terminalStatuses.has(run.status) &&
        !runs.some((candidate) => candidate.run_id === run.run_id)
    );
    this.runsById.clear();
    for (const run of [...runs, ...preserved]) {
      this.runsById.set(run.run_id, run);
    }
    this.emit();
  }

  public upsert(run: RunSummary): void {
    const current = this.runsById.get(run.run_id);
    if (!current || run.version >= current.version) {
      this.runsById.set(run.run_id, run);
      this.emit();
    }
  }

  public apply(event: EventEnvelope): boolean {
    if (event.sequence <= this.highestSequence) {
      return false;
    }
    this.highestSequence = event.sequence;
    this.history.push(event);
    if (this.history.length > this.maximumEvents) {
      this.history.splice(0, this.history.length - this.maximumEvents);
    }
    if (event.run_id) {
      const run = this.runsById.get(event.run_id);
      if (run) {
        const status = statusFromEvent(event.event_type);
        const cancellation = applyCancellationEvent(run.cancellation, event);
        if (status && (!terminalStatuses.has(run.status) || run.status === status)) {
          this.runsById.set(event.run_id, {
            ...run,
            status,
            cancellation,
            updated_at: event.timestamp,
            version: run.version + 1
          });
        } else if (cancellation !== run.cancellation) {
          this.runsById.set(event.run_id, {
            ...run,
            cancellation,
            updated_at: event.timestamp,
            version: run.version + 1
          });
        }
      }
    }
    this.emit();
    return true;
  }

  public runs(): RunSummary[] {
    return [...this.runsById.values()].sort((left, right) =>
      right.updated_at.localeCompare(left.updated_at)
    );
  }

  public run(runId: string): RunSummary | undefined {
    return this.runsById.get(runId);
  }

  public updateCancellation(
    runId: string,
    status: string,
    cancellation: RunSummary["cancellation"]
  ): void {
    const run = this.runsById.get(runId);
    if (!run) return;
    this.runsById.set(runId, {
      ...run,
      status,
      cancellation,
      updated_at: new Date().toISOString(),
      version: run.version + 1
    });
    this.emit();
  }

  public events(): readonly EventEnvelope[] {
    return this.history;
  }

  public subscribe(listener: () => void): { dispose(): void } {
    this.listeners.add(listener);
    return { dispose: () => this.listeners.delete(listener) };
  }

  private emit(): void {
    for (const listener of this.listeners) {
      listener();
    }
  }
}

function statusFromEvent(eventType: string): string | undefined {
  const normalized = eventType.replace(/^durable_/, "");
  const values: Record<string, string> = {
    run_started: "running",
    run_resumed: "running",
    run_waiting_for_approval: "waiting_for_approval",
    run_succeeded: "succeeded",
    run_failed: "failed",
    run_cancelled: "cancelled"
  };
  return values[normalized];
}
