import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import { AgentBusApiError } from "./apiClient";
import { isSafeRepositoryPath } from "./repositoryPath";
import { formatReport } from "./reportPresentation";
import { isSafeControlId } from "./toolPresentation";

export { formatReport } from "./reportPresentation";

export type ClientProvider = () => Promise<AgentBusClient>;

export interface ChangeDocumentIdentity {
  runId: string;
  path: string;
  revision: "before" | "after";
}

export function changeUri(
  identity: ChangeDocumentIdentity
): vscode.Uri {
  if (
    !isSafeRepositoryPath(identity.path) ||
    !isSafeControlId(identity.runId)
  ) {
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
  if (
    !revision ||
    !isSafeControlId(runId) ||
    !isSafeRepositoryPath(path)
  ) {
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
    if (!isSafeControlId(runId)) {
      throw new Error("AgentBus report URI is missing a run ID.");
    }
    return formatReport(await (await this.client()).report(runId));
  }
}

export function reportUri(runId: string): vscode.Uri {
  if (!isSafeControlId(runId)) {
    throw new Error("AgentBus report requires a run ID.");
  }
  return vscode.Uri.from({
    scheme: "agentbus-report",
    path: `/${encodeURIComponent(runId)}`
  });
}
