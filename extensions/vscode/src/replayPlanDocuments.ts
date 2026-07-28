import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import { formatReplayabilityDocument } from "./replayPresentation";
import { isSafeControlId } from "./toolPresentation";

type ReplayMode = "strict" | "offline" | "verify" | "simulate";
const REPLAY_MODES = new Set<ReplayMode>([
  "strict",
  "offline",
  "verify",
  "simulate"
]);

export type ReplayPlanClientProvider = () => Promise<AgentBusClient>;

export function replayPlanUri(
  runId: string,
  mode: ReplayMode
): vscode.Uri {
  if (!isSafeControlId(runId) || !REPLAY_MODES.has(mode)) {
    throw new Error("AgentBus replay plan identity is invalid.");
  }
  return vscode.Uri.from({
    scheme: "agentbus-replay-plan",
    path: `/${encodeURIComponent(runId)}`,
    query: `mode=${mode}`
  });
}

export class ReplayPlanDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  public constructor(private readonly client: ReplayPlanClientProvider) {}

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const runId = decodeURIComponent(uri.path.replace(/^\//, ""));
    const mode = new URLSearchParams(uri.query).get("mode") as
      | ReplayMode
      | null;
    if (
      uri.scheme !== "agentbus-replay-plan" ||
      !isSafeControlId(runId) ||
      !mode ||
      !REPLAY_MODES.has(mode)
    ) {
      throw new Error("Unsafe AgentBus replay plan URI.");
    }
    return formatReplayabilityDocument(
      await (await this.client()).replayability(runId, 0, 500),
      mode
    );
  }
}
