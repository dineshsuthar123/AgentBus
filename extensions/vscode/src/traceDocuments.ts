import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import { formatSpanDocument } from "./tracePresentation";
import { isSafeControlId } from "./toolPresentation";

export type TraceClientProvider = () => Promise<AgentBusClient>;

export interface SpanDocumentIdentity {
  runId: string;
  spanId: string;
}

export function spanUri(runId: string, spanId: string): vscode.Uri {
  if (!isSafeControlId(runId) || !isSafeControlId(spanId)) {
    throw new Error("AgentBus span document requires safe identifiers.");
  }
  return vscode.Uri.from({
    scheme: "agentbus-span",
    path: `/${encodeURIComponent(runId)}/${encodeURIComponent(spanId)}`
  });
}

export function parseSpanUri(uri: vscode.Uri): SpanDocumentIdentity {
  const parts = uri.path
    .split("/")
    .filter(Boolean)
    .map((part) => decodeURIComponent(part));
  const runId = parts[0] ?? "";
  const spanId = parts[1] ?? "";
  if (
    uri.scheme !== "agentbus-span" ||
    parts.length !== 2 ||
    !isSafeControlId(runId) ||
    !isSafeControlId(spanId)
  ) {
    throw new Error("Unsafe AgentBus span document URI.");
  }
  return { runId, spanId };
}

export class SpanDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  public constructor(private readonly client: TraceClientProvider) {}

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const identity = parseSpanUri(uri);
    return formatSpanDocument(
      await (await this.client()).traceSpan(
        identity.runId,
        identity.spanId
      )
    );
  }
}
