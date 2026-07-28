import type {
  ReplaySessionResponse,
  RunReplayabilityResponse,
  TraceCheckpointSummary
} from "./generated/protocol";
import { redactText } from "./redaction";
import { escapeMarkdown } from "./toolPresentation";

export type ReplayGroupKey =
  | "active"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "incompatible"
  | "awaiting_input";

export const REPLAY_GROUPS: ReadonlyArray<{
  key: ReplayGroupKey;
  label: string;
}> = [
  { key: "active", label: "Active" },
  { key: "succeeded", label: "Succeeded" },
  { key: "failed", label: "Failed" },
  { key: "cancelled", label: "Cancelled" },
  { key: "incompatible", label: "Incompatible" },
  { key: "awaiting_input", label: "Awaiting Input" }
];

export function replayGroup(status: string): ReplayGroupKey {
  if (status === "succeeded") return "succeeded";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  if (status === "incompatible") return "incompatible";
  if (status === "awaiting_input") return "awaiting_input";
  return "active";
}

export function replayDescription(session: ReplaySessionResponse): string {
  const fork = session.fork ? " | fork" : "";
  return `${oneLine(session.mode)} | ${oneLine(session.status)}${fork}`;
}

export function replayTooltip(session: ReplaySessionResponse): string {
  return [
    `**Replay ${markdown(session.replay_id)}**`,
    "",
    `Status: \`${markdown(session.status)}\``,
    `Mode: \`${markdown(session.mode)}\``,
    `Source run: \`${markdown(session.source_run_id)}\``,
    `Source trace: \`${markdown(session.source_trace_id)}\``,
    `Checkpoint: \`${markdown(session.from_checkpoint_id ?? "beginning")}\``,
    `Isolated: \`${session.isolated ?? false}\``,
    `Isolation scope: \`${markdown(session.isolation_scope ?? "not required")}\``,
    `Provider calls: \`${session.provider_calls ?? 0}\``,
    `Network calls: \`${session.network_calls ?? 0}\``
  ].join("\n");
}

export function formatReplayDocument(
  session: ReplaySessionResponse
): string {
  const lines = [
    `# AgentBus Replay ${markdown(session.replay_id)}`,
    "",
    `**Status:** ${markdown(session.status)}`,
    "",
    "## Execution",
    "",
    table([
      ["Mode", session.mode],
      ["Source run", session.source_run_id],
      ["Source trace", session.source_trace_id],
      ["Created", session.created_at],
      ["Started", session.started_at ?? "not started"],
      ["Completed", session.completed_at ?? "not completed"],
      ["From span", session.from_span_id ?? "beginning"],
      ["From checkpoint", session.from_checkpoint_id ?? "beginning"],
      ["Fork", session.fork ?? false],
      ["Result trace", session.result_trace_id ?? "not applicable"],
      ["Comparison", session.comparison_id ?? "not applicable"],
      ["Isolated", session.isolated ?? false],
      ["Isolation scope", session.isolation_scope ?? "not required"],
      ["Provider calls", session.provider_calls ?? 0],
      ["Network calls", session.network_calls ?? 0]
    ]),
    "",
    "## Inputs And Policy",
    "",
    bullets("Changed input names", session.changed_input_names ?? []),
    "",
    bullets("Substitutions", session.substitutions ?? []),
    "",
    bullets("Missing inputs", session.missing_inputs ?? []),
    "",
    bullets("Policy drift", session.policy_drift ?? []),
    "",
    "## Span Results",
    "",
    session.span_results?.length
      ? [
          "| Span | Action | Result | Output SHA-256 | Drift |",
          "| --- | --- | --- | --- | --- |",
          ...session.span_results.map(
            (result) =>
              `| \`${markdown(result.span_id)}\` | ${markdown(result.action)} | ${result.succeeded ? "succeeded" : "failed"} | \`${markdown(result.output_sha256 ?? "none")}\` | ${markdown((result.drift ?? []).join(", ") || "none")} |`
          )
        ].join("\n")
      : "_No span results have been recorded yet._",
    "",
    "## Failure",
    "",
    session.failure_category
      ? `${markdown(session.failure_category)}: ${markdown(session.failure_message ?? "No safe diagnostic was returned.")}`
      : "_None._",
    "",
    session.diagnostics_truncated
      ? "_Diagnostics were truncated by the control plane._"
      : "",
    "",
    "_Replay documents contain bounded diagnostics and hashes only. Captured provider payloads are not rendered._",
    ""
  ];
  return redactText(lines.join("\n"), 100_000);
}

export function formatReplayabilityDocument(
  replayability: RunReplayabilityResponse,
  mode: "strict" | "offline" | "verify" | "simulate"
): string {
  const isolated = replayability.spans.some(
    (span) => span.requires_isolated_workspace
  );
  const replayable = replayability.spans.filter(
    (span) => span.level !== "non_replayable"
  ).length;
  const blocked = replayability.spans.length - replayable;
  const lines = [
    `# AgentBus Replay Plan ${markdown(replayability.run_id)}`,
    "",
    "## Selection",
    "",
    table([
      ["Mode", mode],
      ["Trace", replayability.trace_id],
      ["Replayability", replayability.level],
      ["Replayable offline", replayability.replayable_offline],
      [
        "Live-provider consent required",
        replayability.live_provider_consent_required ?? false
      ],
      ["Replayable spans", replayable],
      ["Non-replayable spans", blocked],
      [
        "Isolation location",
        isolated
          ? "daemon_managed_temporary_workspace"
          : "not required"
      ]
    ]),
    "",
    "## Expected Side Effects",
    "",
    "- The source repository is never mutated by offline replay.",
    "- Captured provider responses are substituted; live providers and network access remain disabled.",
    isolated
      ? "- Replay-safe tool work is constrained to a daemon-managed temporary workspace."
      : "- This plan does not require a replay workspace.",
    "- Mutating tool results are reused or simulated unless an explicit safe strategy says otherwise.",
    "- Current policy is evaluated and any policy drift is recorded in the replay session.",
    "",
    "## Missing Inputs",
    "",
    replayability.missing_input_hashes?.length
      ? replayability.missing_input_hashes
          .map((hash) => `- \`${markdown(hash)}\``)
          .join("\n")
      : "_None._",
    "",
    "## Trace Reasons",
    "",
    replayability.reasons?.length
      ? replayability.reasons
          .map((reason) => `- ${markdown(reason)}`)
          .join("\n")
      : "_None._",
    "",
    "## Span Replayability",
    "",
    replayability.spans.length
      ? [
          "| Span | Type | Level | Substitutions | Isolated | Live consent | Reasons |",
          "| --- | --- | --- | --- | --- | --- | --- |",
          ...replayability.spans.map(
            (span) =>
              `| \`${markdown(span.span_id)}\` | ${markdown(span.span_type)} | ${markdown(span.level)} | ${markdown((span.substitution_kinds ?? []).join(", ") || "none")} | ${span.requires_isolated_workspace ?? false} | ${span.live_provider_consent_required ?? false} | ${markdown(span.reasons.join("; ") || "none")} |`
          )
        ].join("\n")
      : "_No span classifications were returned._",
    "",
    replayability.truncated
      ? "_Span replayability was truncated by the control plane._"
      : "",
    "",
    "_This plan never includes captured payload values, prompts, credentials, or private paths._",
    ""
  ];
  return redactText(lines.join("\n"), 100_000);
}

export function offlineReplayBlockReason(
  replayability: RunReplayabilityResponse
): string | undefined {
  if (replayability.replayable_offline) return undefined;
  const reasons = (replayability.reasons ?? []).slice(0, 5);
  const missing = replayability.missing_input_hashes?.length ?? 0;
  return [
    "This trace is not replayable in offline mode.",
    ...reasons,
    missing ? `${missing} required captured input(s) are unavailable.` : ""
  ]
    .filter(Boolean)
    .join(" ");
}

export function isTerminalReplayStatus(status: string): boolean {
  return [
    "succeeded",
    "failed",
    "cancelled",
    "incompatible",
    "awaiting_input"
  ].includes(status);
}

export const FORK_INPUT_NAMES = [
  "approval_decisions",
  "deterministic_provider_profile",
  "model_route",
  "policy_configuration",
  "resource_budgets",
  "retry_limit",
  "selected_source_patch",
  "task_text",
  "tool_response"
] as const;

export type ForkInputName = (typeof FORK_INPUT_NAMES)[number];

export interface ValidatedForkInputs {
  changedInputs: Record<string, unknown>;
  changedInputNames: string[];
  liveProviderRequested: boolean;
}

export function validateForkInputs(
  value: Record<string, unknown>
): ValidatedForkInputs {
  const names = Object.keys(value).sort();
  if (names.length === 0) {
    throw new Error("A fork must change at least one input.");
  }
  const allowed = new Set<string>(FORK_INPUT_NAMES);
  const unknown = names.filter((name) => !allowed.has(name));
  if (unknown.length) {
    throw new Error(`Unsupported fork input: ${unknown.join(", ")}.`);
  }
  let serialized: string;
  try {
    serialized = JSON.stringify(value);
  } catch {
    throw new Error("Fork inputs must be finite JSON.");
  }
  if (!serialized || new TextEncoder().encode(serialized).byteLength > 65_536) {
    throw new Error("Fork inputs exceed the 64 KiB protocol limit.");
  }
  const route = recordValue(value.model_route);
  const provider = String(route?.provider ?? "").toLowerCase();
  return {
    changedInputs: JSON.parse(serialized) as Record<string, unknown>,
    changedInputNames: names,
    liveProviderRequested: ["azure", "ollama"].includes(provider)
  };
}

export function parseForkInput(
  name: ForkInputName,
  json: string
): ValidatedForkInputs {
  let value: unknown;
  try {
    value = JSON.parse(json);
  } catch {
    throw new Error("Fork input must be valid JSON.");
  }
  return validateForkInputs({ [name]: value });
}

export function replayableCheckpoints(
  checkpoints: readonly TraceCheckpointSummary[]
): TraceCheckpointSummary[] {
  return checkpoints
    .filter((checkpoint) => checkpoint.replayable)
    .sort((left, right) => left.sequence - right.sequence);
}

function recordValue(
  value: unknown
): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function table(rows: ReadonlyArray<readonly [string, unknown]>): string {
  return [
    "| Field | Value |",
    "| --- | --- |",
    ...rows.map(
      ([name, value]) => `| ${markdown(name)} | ${markdown(value)} |`
    )
  ].join("\n");
}

function bullets(label: string, values: readonly string[]): string {
  if (values.length === 0) return `**${label}:** none`;
  return [
    `**${label}:**`,
    ...values.slice(0, 500).map((value) => `- ${markdown(value)}`)
  ].join("\n");
}

function markdown(value: unknown): string {
  return escapeMarkdown(oneLine(value));
}

function oneLine(value: unknown): string {
  return redactText(value, 2_000).replace(/[\r\n]+/g, " ");
}
