import type { ReplaySessionResponse } from "./generated/protocol";
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
      ["Isolated", session.isolated ?? false],
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
