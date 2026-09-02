import type {
  ApprovalListResponse,
  AttemptListResponse,
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
import { StudioClient, type ReadRequestOptions } from "./client";

export interface RunBundle {
  run: RunSummary;
  tasks: TaskListResponse;
  approvals: ApprovalListResponse;
  attempts?: AttemptListResponse;
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
  partialErrors?: Readonly<Record<string, string>>;
}

export async function loadRunBundle(
  client: StudioClient,
  runId: string,
  options: ReadRequestOptions = {}
): Promise<RunBundle> {
  const errors: Record<string, string> = {};
  const [run, tasks, approvals] = await Promise.all([
    client.run(runId, options),
    client.tasks(runId, options),
    client.approvals(runId, options)
  ]);
  const [attempts, report, changes, diff, invocations, audit, trace, spans, scheduler, usage, worktrees] =
    await Promise.all([
      optional("attempts", client.attempts(runId, options), errors),
      optional("report", client.report(runId, options), errors),
      optional("changes", client.changes(runId, options), errors),
      optional("diff", client.diff(runId, undefined, options), errors),
      optional("invocations", client.invocations(runId, options), errors),
      optional("audit", client.audit(runId, options), errors),
      optional("trace", client.trace(runId, options), errors),
      optional("spans", client.traceSpans(runId, options), errors),
      optional("scheduler", client.scheduler(runId, options), errors),
      optional("usage", client.usage(runId, options), errors),
      optional("worktrees", client.worktrees(runId, options), errors)
    ]);
  const [provenance, replayability, replays] = trace
    ? await Promise.all([
        optional("provenance", client.provenance(runId, options), errors),
        optional("replayability", client.replayability(runId, options), errors),
        optional("replays", client.replays(trace.trace_id, options), errors)
      ])
    : [undefined, undefined, undefined];
  return {
    run,
    tasks,
    approvals,
    attempts,
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
    worktrees,
    partialErrors: Object.keys(errors).length ? Object.freeze(errors) : undefined
  };
}

async function optional<T>(
  name: string,
  request: Promise<T>,
  errors: Record<string, string>
): Promise<T | undefined> {
  try {
    return await request;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    errors[name] = error instanceof Error ? error.message : `${name} is unavailable.`;
    return undefined;
  }
}
