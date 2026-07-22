import type {
  ToolCapability,
  ToolInvocationDetail,
  ToolPolicyResponse,
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

export function isSafeControlId(value: string): boolean {
  return Boolean(
    value &&
      value.length <= 128 &&
      value !== "." &&
      value !== ".." &&
      !/[\0/\\\r\n]/.test(value)
  );
}

export function formatToolInvocation(
  invocation: ToolInvocationDetail
): string {
  const decision = invocation.policy_decision;
  const result = invocation.result;
  const lines = [
    `# ${escapeMarkdown(invocation.tool_name)}`,
    "",
    markdownTable([
      ["Status", invocation.status],
      ["Run", invocation.run_id],
      ["Task", invocation.task_id],
      ["Invocation", invocation.invocation_id],
      ["Revision", invocation.invocation_revision],
      ["Tool version", toolVersion(invocation)],
      ["Protocol", invocation.protocol_version],
      ["Caller", invocation.caller_role],
      ["Duration", toolDuration(invocation)],
      ["Approval", invocation.approval_id ?? "none"],
      ["Policy", decision?.outcome ?? "not evaluated"],
      ["Policy rule", decision?.rule_id ?? "n/a"]
    ]),
    "",
    "## Policy",
    "",
    decision
      ? indentedJson({
          outcome: decision.outcome,
          rule_id: decision.rule_id,
          reason: decision.reason,
          constraints: decision.constraints ?? [],
          evaluated_at: decision.evaluated_at
        })
      : "Not evaluated.",
    "",
    "## Capabilities",
    "",
    indentedJson(invocation.capabilities),
    "",
    "## Resources",
    "",
    indentedJson({
      budget: invocation.resource_budget,
      usage: invocation.resource_usage
    }),
    "",
    "## Cancellation",
    "",
    indentedJson(invocation.cancellation),
    "",
    "## Result",
    "",
    indentedJson(
      result
        ? {
            status: result.status,
            exit_code: result.exit_code,
            duration_seconds: result.duration_seconds,
            timed_out: result.timed_out,
            stdout_truncated: result.stdout_truncated,
            stderr_truncated: result.stderr_truncated,
            error: result.error,
            structured_output_summary: result.structured_output,
            safe_diagnostic_metadata: result.safe_diagnostic_metadata,
            artifacts: result.artifacts ?? []
          }
        : null
    ),
    "",
    "_Raw tool arguments, stdout, stderr, and unrestricted output bodies are not displayed._",
    ""
  ];
  return lines.join("\n");
}

export function formatToolPolicy(policy: ToolPolicyResponse): string {
  const rows = policy.rules.map((rule) => [
    rule.rule_id ?? "unknown",
    rule.outcome ?? "unknown",
    rule.description ?? ""
  ]);
  return [
    `# Tool Policy ${escapeMarkdown(policy.policy_id ?? "agentbus-default-v1")}`,
    "",
    "## Outcomes",
    "",
    policy.outcomes.map((outcome) => `- ${escapeMarkdown(outcome)}`).join("\n"),
    "",
    "## Rules",
    "",
    markdownTable(rows, ["Rule", "Outcome", "Description"]),
    "",
    "## Configuration",
    "",
    indentedJson(policy.configuration),
    ""
  ].join("\n");
}

function markdownTable(
  rows: Array<Array<string | number>>,
  headers: string[] = ["Field", "Value"]
): string {
  const values = [
    `| ${headers.map(escapeMarkdown).join(" | ")} |`,
    `| ${headers.map(() => "---").join(" | ")} |`
  ];
  for (const row of rows) {
    values.push(
      `| ${row.map((value) => escapeMarkdown(String(value))).join(" | ")} |`
    );
  }
  return values.join("\n");
}

function indentedJson(value: unknown): string {
  return JSON.stringify(value ?? null, null, 2)
    .split("\n")
    .map((line) => `    ${line}`)
    .join("\n");
}

function formatSeconds(value: number): string {
  if (value < 0.001) return "<1ms";
  if (value < 1) return `${Math.round(value * 1000)}ms`;
  return `${value.toFixed(value < 10 ? 2 : 1)}s`;
}
