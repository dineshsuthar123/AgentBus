import type { RunReportResponse } from "./generated/protocol";
import { cancellationDetails } from "./cancellation";
import { redactText } from "./redaction";
import { escapeMarkdown } from "./toolPresentation";

const MAX_REPORT_SECTION_CHARS = 100_000;

export function formatReport(response: RunReportResponse): string {
  const report = response.report;
  const lines = [
    `# AgentBus Run ${inline(response.run_id)}`,
    "",
    `**Status:** ${inline(response.status)}`,
    "",
    "## Summary",
    "",
    tableRows(report, [
      "workspace",
      "verifier_status",
      "reviewer_status",
      "failure_reason",
      "commit_identifier",
      "pr_url"
    ]),
    "",
    "## Tasks",
    "",
    indentedJson(report.graph_progress),
    "",
    "## Changes",
    "",
    indentedJson(report.changed_files),
    "",
    "## Tool Runtime",
    "",
    indentedJson(report.tool_runtime),
    "",
    "## Cancellation",
    "",
    indentedJson({
      state: cancellationDetails(response.cancellation, response.status),
      lifecycle: response.cancellation
    }),
    "",
    "## Retries And Workers",
    "",
    indentedJson({
      attempts_per_task: report.attempts_per_task,
      workers_used: report.workers_used,
      current_leases: report.current_leases,
      expired_leases: report.expired_leases,
      integration_conflicts: report.integration_conflicts
    }),
    "",
    "## Review",
    "",
    indentedJson({
      reviewer_summary: report.reviewer_summary,
      reviewer_issues: report.reviewer_issues,
      required_fixes: report.required_fixes
    }),
    "",
    "## Recovery",
    "",
    indentedJson({
      resume_command: report.resume_command,
      cleanup_recommendations: report.cleanup_recommendations,
      side_effects_persisted: report.side_effects_persisted
    }),
    "",
    "_Filesystem edits are not automatically rolled back after a failed run._",
    ""
  ];
  return lines.join("\n");
}

function tableRows(
  value: Record<string, unknown>,
  keys: string[]
): string {
  const rows = ["| Field | Value |", "| --- | --- |"];
  for (const key of keys) {
    rows.push(`| ${key} | ${inline(value[key])} |`);
  }
  return rows.join("\n");
}

function inline(value: unknown): string {
  const redacted = redactText(value ?? "n/a", 2_000).replace(/[\r\n]+/g, " ");
  return escapeMarkdown(redacted);
}

function indentedJson(value: unknown): string {
  const serialized = JSON.stringify(value ?? null, null, 2);
  return redactText(serialized, MAX_REPORT_SECTION_CHARS)
    .split("\n")
    .map((line) => `    ${line}`)
    .join("\n");
}
