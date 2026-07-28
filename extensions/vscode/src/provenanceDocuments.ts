import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import { formatProvenanceReport } from "./provenancePresentation";
import { isSafeControlId } from "./toolPresentation";

export type ProvenanceClientProvider = () => Promise<AgentBusClient>;

export function provenanceUri(runId: string): vscode.Uri {
  if (!isSafeControlId(runId)) {
    throw new Error("AgentBus provenance requires a safe run identifier.");
  }
  return vscode.Uri.from({
    scheme: "agentbus-provenance",
    path: `/${encodeURIComponent(runId)}`
  });
}

export class ProvenanceDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  public constructor(private readonly client: ProvenanceClientProvider) {}

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const runId = decodeURIComponent(uri.path.replace(/^\//, ""));
    if (uri.scheme !== "agentbus-provenance" || !isSafeControlId(runId)) {
      throw new Error("Unsafe AgentBus provenance document URI.");
    }
    const client = await this.client();
    const [provenance, trace, replayability, runReport] = await Promise.all([
      client.provenance(runId),
      client.trace(runId),
      client.replayability(runId, 0, 500),
      client.report(runId)
    ]);
    return formatProvenanceReport({
      provenance,
      trace,
      replayability,
      runReport
    });
  }
}
