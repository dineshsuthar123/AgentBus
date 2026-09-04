import type { EventEnvelope } from "../api/types";
import type { RunBundle } from "../api/runBundle";
import { humanize } from "../lib/format";

export type TimelineSource = "event" | "span" | "attempt" | "tool" | "approval";

export interface TimelineRecord {
  actor: string;
  event?: EventEnvelope;
  id: string;
  nodeId?: string;
  sequence?: number;
  source: TimelineSource;
  status: string;
  summary: string;
  taskId?: string;
  timestamp: string;
  title: string;
}

export interface TimelineTick {
  count: number;
  record: TimelineRecord;
}

export function buildTimeline(bundle: RunBundle, events: readonly EventEnvelope[]): readonly TimelineRecord[] {
  if (events.length) {
    return Object.freeze(events.map((event) => ({
      actor: event.worker_id ?? actorFromType(event.event_type),
      event,
      id: `event:${event.sequence}`,
      sequence: event.sequence,
      source: "event" as const,
      status: payloadStatus(event.payload) ?? statusFromType(event.event_type),
      summary: boundedSummary(event.payload),
      taskId: event.task_id ?? undefined,
      timestamp: event.timestamp,
      title: humanize(event.event_type)
    })));
  }

  const records: TimelineRecord[] = [];
  for (const span of bundle.spans?.spans ?? []) {
    records.push({
      actor: humanize(span.span_type),
      id: `span:${span.span_id}`,
      nodeId: nodeForSpan(bundle, span.span_type, span.task_id, span.invocation_id),
      sequence: span.sequence,
      source: "span",
      status: span.status,
      summary: span.failure?.message ?? `${span.output_count ?? 0} outputs / ${span.artifact_count ?? 0} artifacts`,
      taskId: span.task_id ?? undefined,
      timestamp: span.started_at,
      title: span.name
    });
  }
  for (const attempt of bundle.attempts?.attempts ?? []) {
    records.push({
      actor: "Coder attempt",
      id: `attempt:${attempt.attempt_id}`,
      nodeId: `coder:${attempt.task_id}:${attempt.attempt_number}`,
      source: "attempt",
      status: attempt.status,
      summary: attempt.failure_message ?? attempt.observation_summary ?? `Attempt ${attempt.attempt_number}`,
      taskId: attempt.task_id,
      timestamp: attempt.started_at,
      title: `Attempt ${attempt.attempt_number}`
    });
  }
  for (const invocation of bundle.invocations?.invocations ?? []) {
    records.push({
      actor: humanize(invocation.caller_role),
      id: `tool:${invocation.invocation_id}:${invocation.invocation_revision}`,
      nodeId: `tool:${invocation.invocation_id}:${invocation.invocation_revision}`,
      sequence: invocation.invocation_sequence,
      source: "tool",
      status: invocation.status,
      summary: invocation.capabilities.map((capability) => capability.name).join(" | ") || "Managed invocation",
      taskId: invocation.task_id,
      timestamp: invocation.requested_at,
      title: invocation.tool_name
    });
  }
  for (const approval of bundle.approvals.approvals) {
    records.push({
      actor: "Policy gate",
      id: `approval:${approval.approval_id}`,
      nodeId: `approval:${approval.approval_id}`,
      source: "approval",
      status: approval.state,
      summary: approval.reason ?? approval.requested_action,
      taskId: approval.task_id,
      timestamp: approval.created_at,
      title: approval.requested_action
    });
  }
  records.sort((left, right) => {
    const time = new Date(left.timestamp).valueOf() - new Date(right.timestamp).valueOf();
    return time || (left.sequence ?? 0) - (right.sequence ?? 0) || left.id.localeCompare(right.id);
  });
  return Object.freeze(records);
}

export function sampleTimeline(records: readonly TimelineRecord[], maximumTicks = 180): readonly TimelineTick[] {
  if (records.length <= maximumTicks) return records.map((record) => ({ count: 1, record }));
  const bucketSize = Math.ceil(records.length / maximumTicks);
  const ticks: TimelineTick[] = [];
  for (let index = 0; index < records.length; index += bucketSize) {
    const bucket = records.slice(index, index + bucketSize);
    ticks.push({ count: bucket.length, record: bucket.at(-1)! });
  }
  return ticks;
}

function nodeForSpan(bundle: RunBundle, spanType: string, taskId?: string | null, invocationId?: string | null): string | undefined {
  if (invocationId) {
    const invocation = bundle.invocations?.invocations.find((item) => item.invocation_id === invocationId);
    return invocation ? `tool:${invocation.invocation_id}:${invocation.invocation_revision}` : undefined;
  }
  if (spanType.includes("planner")) return "planner";
  if (spanType.includes("reviewer")) return taskId ? `task-reviewer:${taskId}` : "final-reviewer";
  if (spanType.includes("verifier") && taskId) {
    const attempt = bundle.attempts?.attempts.filter((item) => item.task_id === taskId).at(-1);
    return `verifier:${taskId}:${attempt?.attempt_number ?? 1}`;
  }
  return undefined;
}

function actorFromType(eventType: string): string {
  return humanize(eventType.split(/[.:]/, 1)[0] || "Syndra");
}

function payloadStatus(payload?: Record<string, unknown>): string | undefined {
  for (const key of ["status", "state", "outcome", "decision"]) {
    if (typeof payload?.[key] === "string") return String(payload[key]);
  }
  return undefined;
}

function statusFromType(eventType: string): string {
  const value = eventType.toLowerCase();
  if (value.includes("failed") || value.includes("rejected") || value.includes("denied")) return "failed";
  if (value.includes("completed") || value.includes("succeeded") || value.includes("approved") || value.includes("sealed")) return "succeeded";
  if (value.includes("approval") || value.includes("waiting")) return "waiting_for_approval";
  return "observed";
}

function boundedSummary(payload?: Record<string, unknown>): string {
  if (!payload) return "No bounded payload fields.";
  for (const key of ["summary", "message", "reason", "failure_message", "tool_name", "task_id"]) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.slice(0, 240);
  }
  const keys = Object.keys(payload).slice(0, 5);
  return keys.length ? `Fields: ${keys.join(", ")}` : "No bounded payload fields.";
}
