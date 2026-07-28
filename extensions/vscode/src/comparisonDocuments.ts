import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import { formatComparisonDocument } from "./comparisonPresentation";
import { isSafeControlId } from "./toolPresentation";

export type ComparisonClientProvider = () => Promise<AgentBusClient>;

export function comparisonUri(comparisonId: string): vscode.Uri {
  if (!isSafeControlId(comparisonId)) {
    throw new Error("AgentBus comparison requires a safe identifier.");
  }
  return vscode.Uri.from({
    scheme: "agentbus-comparison",
    path: `/${encodeURIComponent(comparisonId)}`
  });
}

export class ComparisonDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  public constructor(private readonly client: ComparisonClientProvider) {}

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const comparisonId = decodeURIComponent(uri.path.replace(/^\//, ""));
    if (
      uri.scheme !== "agentbus-comparison" ||
      !isSafeControlId(comparisonId)
    ) {
      throw new Error("Unsafe AgentBus comparison document URI.");
    }
    return formatComparisonDocument(
      await (await this.client()).comparison(comparisonId, 0, 500)
    );
  }
}
