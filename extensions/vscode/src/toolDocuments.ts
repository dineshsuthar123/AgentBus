import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import {
  formatToolInvocation,
  formatToolPolicy,
  isSafeControlId
} from "./toolPresentation";

type ClientProvider = () => Promise<AgentBusClient>;

interface ToolDocumentIdentity {
  runId: string;
  invocationId: string;
}

export function toolInvocationUri(
  runId: string,
  invocationId: string
): vscode.Uri {
  if (!isSafeControlId(runId) || !isSafeControlId(invocationId)) {
    throw new Error("Unsafe AgentBus tool invocation identity.");
  }
  return vscode.Uri.from({
    scheme: "agentbus-tool",
    path: `/${encodeURIComponent(runId)}`,
    query: `invocation=${encodeURIComponent(invocationId)}`
  });
}

export function toolPolicyUri(): vscode.Uri {
  return vscode.Uri.from({ scheme: "agentbus-policy", path: "/default" });
}

export class ToolInvocationDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  public constructor(private readonly client: ClientProvider) {}

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const identity = parseToolInvocationUri(uri);
    return formatToolInvocation(
      await (await this.client()).toolInvocation(
        identity.runId,
        identity.invocationId
      )
    );
  }
}

export class ToolPolicyDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  public constructor(private readonly client: ClientProvider) {}

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    if (uri.scheme !== "agentbus-policy" || uri.path !== "/default") {
      throw new Error("Unsafe AgentBus tool policy document identity.");
    }
    return formatToolPolicy(await (await this.client()).toolPolicy());
  }
}

function parseToolInvocationUri(uri: vscode.Uri): ToolDocumentIdentity {
  const runId = decodeURIComponent(uri.path.replace(/^\//, ""));
  const invocationId = new URLSearchParams(uri.query).get("invocation") ?? "";
  if (
    uri.scheme !== "agentbus-tool" ||
    !isSafeControlId(runId) ||
    !isSafeControlId(invocationId)
  ) {
    throw new Error("Unsafe AgentBus tool invocation document identity.");
  }
  return { runId, invocationId };
}
