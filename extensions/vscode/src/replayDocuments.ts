import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import { formatReplayDocument } from "./replayPresentation";
import { isSafeControlId } from "./toolPresentation";

export type ReplayClientProvider = () => Promise<AgentBusClient>;

export function replayUri(replayId: string): vscode.Uri {
  if (!isSafeControlId(replayId)) {
    throw new Error("AgentBus replay document requires a safe identifier.");
  }
  return vscode.Uri.from({
    scheme: "agentbus-replay",
    path: `/${encodeURIComponent(replayId)}`
  });
}

export class ReplayDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  public constructor(private readonly client: ReplayClientProvider) {}

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const replayId = decodeURIComponent(uri.path.replace(/^\//, ""));
    if (uri.scheme !== "agentbus-replay" || !isSafeControlId(replayId)) {
      throw new Error("Unsafe AgentBus replay document URI.");
    }
    return formatReplayDocument(await (await this.client()).replay(replayId));
  }
}
