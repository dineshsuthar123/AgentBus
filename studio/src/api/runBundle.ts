import type {
  ApprovalListResponse,
  ChangeListResponse,
  DiffResponse,
  ProvenanceResponse,
  ReplayListResponse,
  RunReplayabilityResponse,
  RunReportResponse,
  RunSummary,
  SchedulerResponse,
  TaskListResponse,
  ToolAuditListResponse,
  ToolInvocationListResponse,
  TraceResponse,
  TraceSpanListResponse,
  UsageResponse,
  WorktreeListResponse
} from "./types";
import { StudioClient } from "./client";

export interface RunBundle {
  run: RunSummary;
  tasks: TaskListResponse;
  approvals: ApprovalListResponse;
  report?: RunReportResponse;
  changes?: ChangeListResponse;
  diff?: DiffResponse;
  invocations?: ToolInvocationListResponse;
  audit?: ToolAuditListResponse;
  trace?: TraceResponse;
  spans?: TraceSpanListResponse;
  provenance?: ProvenanceResponse;
  replayability?: RunReplayabilityResponse;
  replays?: ReplayListResponse;
  scheduler?: SchedulerResponse;
  usage?: UsageResponse;
  worktrees?: WorktreeListResponse;
}

export async function loadRunBundle(client: StudioClient, runId: string): Promise<RunBundle> {
  const [run, tasks, approvals] = await Promise.all([
    client.run(runId),
    client.tasks(runId),
    client.approvals(runId)
  ]);
  const [report, changes, diff, invocations, audit, trace, spans, scheduler, usage, worktrees] =
    await Promise.all([
      optional(client.report(runId)),
      optional(client.changes(runId)),
      optional(client.diff(runId)),
      optional(client.invocations(runId)),
      optional(client.audit(runId)),
      optional(client.trace(runId)),
      optional(client.traceSpans(runId)),
      optional(client.scheduler(runId)),
      optional(client.usage(runId)),
      optional(client.worktrees(runId))
    ]);
  const [provenance, replayability, replays] = trace
    ? await Promise.all([
        optional(client.provenance(runId)),
        optional(client.replayability(runId)),
        optional(client.replays(trace.trace_id))
      ])
    : [undefined, undefined, undefined];
  return {
    run,
    tasks,
    approvals,
    report,
    changes,
    diff,
    invocations,
    audit,
    trace,
    spans,
    provenance,
    replayability,
    replays,
    scheduler,
    usage,
    worktrees
  };
}

async function optional<T>(request: Promise<T>): Promise<T | undefined> {
  try {
    return await request;
  } catch {
    return undefined;
  }
}
