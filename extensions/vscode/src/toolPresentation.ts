import type {
  ToolCapability,
  ToolInvocationSummary
} from "./generated/protocol";

export type ToolGroupKey =
  | "active"
  | "awaiting_approval"
  | "succeeded"
  | "failed"
  | "denied"
  | "cancelled"
  | "timed_out";

export const TOOL_GROUPS: ReadonlyArray<{
  key: ToolGroupKey;
  label: string;
}> = [
  { key: "active", label: "Active" },
  { key: "awaiting_approval", label: "Awaiting Approval" },
  { key: "succeeded", label: "Succeeded" },
  { key: "failed", label: "Failed" },
  { key: "denied", label: "Denied" },
  { key: "cancelled", label: "Cancelled" },
  { key: "timed_out", label: "Timed Out" }
];

export function toolGroup(status: string): ToolGroupKey {
  if (status === "awaiting_approval") return "awaiting_approval";
  if (status === "succeeded") return "succeeded";
  if (status === "failed") return "failed";
  if (status === "denied") return "denied";
  if (status === "cancelled") return "cancelled";
  if (status === "timed_out") return "timed_out";
  return "active";
}

export function canCancelTool(status: string): boolean {
  return toolGroup(status) === "active" || status === "awaiting_approval";
}

export function toolVersion(invocation: ToolInvocationSummary): string {
  const version = invocation.tool_version;
  return `${version.major}.${version.minor ?? 0}.${version.patch ?? 0}`;
}

export function capabilityNames(capabilities: ToolCapability[]): string {
  return capabilities.map((item) => item.name).join(", ") || "none";
}

export function toolDuration(invocation: ToolInvocationSummary): string {
  const observed = invocation.resource_usage.wall_clock_seconds ?? 0;
  if (observed > 0) return formatSeconds(observed);
  if (!invocation.started_at) return "not started";
  const end = invocation.completed_at ?? invocation.updated_at;
  const elapsed = Date.parse(end) - Date.parse(invocation.started_at);
  return Number.isFinite(elapsed) && elapsed >= 0
    ? formatSeconds(elapsed / 1000)
    : "unavailable";
}

export function toolResourceSummary(
  invocation: ToolInvocationSummary
): string {
  const usage = invocation.resource_usage;
  const output = (usage.stdout_bytes ?? 0) + (usage.stderr_bytes ?? 0);
  return [
    `${toolDuration(invocation)} wall`,
    `${output} B output`,
    `${usage.file_mutations ?? 0} mutation(s)`,
    `${usage.child_processes ?? 0} child process(es)`
  ].join(" | ");
}

export function escapeMarkdown(value: string): string {
  return value.replace(/[\\`*_{}[\]()<>#+.!|~-]/g, "\\$&");
}

function formatSeconds(value: number): string {
  if (value < 0.001) return "<1ms";
  if (value < 1) return `${Math.round(value * 1000)}ms`;
  return `${value.toFixed(value < 10 ? 2 : 1)}s`;
}
