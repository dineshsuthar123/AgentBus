import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import { AgentBusApiError } from "./apiClient";
import type { RunReportResponse } from "./generated/protocol";
import { isSafeRepositoryPath } from "./repositoryPath";
import { cancellationDetails } from "./cancellation";

export type ClientProvider = () => Promise<AgentBusClient>;

export interface ChangeDocumentIdentity {
  runId: string;
  path: string;
  revision: "before" | "after";
}

export function changeUri(
  identity: ChangeDocumentIdentity
): vscode.Uri {
  if (!isSafeRepositoryPath(identity.path) || !identity.runId) {
    throw new Error("Unsafe AgentBus change document identity.");
  }
  return vscode.Uri.from({
    scheme: `agentbus-${identity.revision}`,
    path: `/${encodeURIComponent(identity.runId)}`,
    query: `path=${encodeURIComponent(identity.path)}`
  });
}

export function parseChangeUri(uri: {
  scheme: string;
  path: string;
  query: string;
}): ChangeDocumentIdentity {
  const revision =
    uri.scheme === "agentbus-before"
      ? "before"
      : uri.scheme === "agentbus-after"
        ? "after"
        : undefined;
  const runId = decodeURIComponent(uri.path.replace(/^\//, ""));
  const path = new URLSearchParams(uri.query).get("path") ?? "";
  if (!revision || !runId || !isSafeRepositoryPath(path)) {
    throw new Error("Unsafe AgentBus virtual document URI.");
  }
  return { runId, path, revision };
}

export class ChangeDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  public constructor(private readonly client: ClientProvider) {}

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const identity = parseChangeUri(uri);
    try {
      return (
        await (await this.client()).file(
          identity.runId,
          identity.path,
          identity.revision
        )
      ).content;
    } catch (error) {
      if (
        identity.revision === "before" &&
        error instanceof AgentBusApiError &&
        error.status === 404
      ) {
        return "";
      }
      throw error;
    }
  }
}

export class ReportDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  private readonly changed = new vscode.EventEmitter<vscode.Uri>();
  public readonly onDidChange = this.changed.event;

  public constructor(private readonly client: ClientProvider) {}

  public refresh(uri: vscode.Uri): void {
    this.changed.fire(uri);
  }

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const runId = decodeURIComponent(uri.path.replace(/^\//, ""));
    if (!runId) {
      throw new Error("AgentBus report URI is missing a run ID.");
    }
    return formatReport(await (await this.client()).report(runId));
  }
}

export function reportUri(runId: string): vscode.Uri {
  if (!runId) {
    throw new Error("AgentBus report requires a run ID.");
  }
  return vscode.Uri.from({
    scheme: "agentbus-report",
    path: `/${encodeURIComponent(runId)}`
  });
}

export function formatReport(response: RunReportResponse): string {
  const report = response.report;
  const lines = [
    `# AgentBus Run ${response.run_id}`,
    "",
    `**Status:** ${response.status}`,
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
    listValue(report.graph_progress),
    "",
    "## Changes",
    "",
    listValue(report.changed_files),
    "",
    "## Cancellation",
    "",
    listValue({
      state: cancellationDetails(response.cancellation, response.status),
      lifecycle: response.cancellation
    }),
    "",
    "## Retries And Workers",
    "",
    listValue({
      attempts_per_task: report.attempts_per_task,
      workers_used: report.workers_used,
      current_leases: report.current_leases,
      expired_leases: report.expired_leases,
      integration_conflicts: report.integration_conflicts
    }),
    "",
    "## Review",
    "",
    listValue({
      reviewer_summary: report.reviewer_summary,
      reviewer_issues: report.reviewer_issues,
      required_fixes: report.required_fixes
    }),
    "",
    "## Recovery",
    "",
    listValue({
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
  return String(value ?? "n/a").replaceAll("|", "\\|").replaceAll("\n", " ");
}

function listValue(value: unknown): string {
  return `\`\`\`json\n${JSON.stringify(value ?? null, null, 2)}\n\`\`\``;
}
