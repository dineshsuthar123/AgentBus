import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import {
  formatComparisonDocument,
  formatComparisonSide
} from "./comparisonPresentation";
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

export function comparisonSideUri(
  comparisonId: string,
  side: "left" | "right"
): vscode.Uri {
  if (!isSafeControlId(comparisonId)) {
    throw new Error("AgentBus comparison requires a safe identifier.");
  }
  return vscode.Uri.from({
    scheme: "agentbus-comparison-side",
    path: `/${encodeURIComponent(comparisonId)}/${side}.json`
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

export class ComparisonSideDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  public constructor(private readonly client: ComparisonClientProvider) {}

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const parts = uri.path
      .split("/")
      .filter(Boolean)
      .map((part) => decodeURIComponent(part));
    const comparisonId = parts[0] ?? "";
    const side =
      parts[1] === "left.json"
        ? "left"
        : parts[1] === "right.json"
          ? "right"
          : undefined;
    if (
      uri.scheme !== "agentbus-comparison-side" ||
      parts.length !== 2 ||
      !side ||
      !isSafeControlId(comparisonId)
    ) {
      throw new Error("Unsafe AgentBus comparison-side document URI.");
    }
    return formatComparisonSide(
      await (await this.client()).comparison(comparisonId, 0, 500),
      side
    );
  }
}
