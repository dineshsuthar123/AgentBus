import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import { formatMcpServer } from "./mcpPresentation";
import { isSafeControlId } from "./toolPresentation";

type ClientProvider = () => Promise<AgentBusClient>;

export function mcpServerUri(serverId: string): vscode.Uri {
  if (!isSafeControlId(serverId)) {
    throw new Error("Unsafe AgentBus MCP server identity.");
  }
  return vscode.Uri.from({
    scheme: "agentbus-mcp",
    path: `/${encodeURIComponent(serverId)}`
  });
}

export class McpServerDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  public constructor(private readonly client: ClientProvider) {}

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const serverId = parseServerUri(uri);
    const response = await (await this.client()).mcpServers();
    const server = response.servers.find(
      (candidate) => candidate.server_id === serverId
    );
    if (!server) {
      throw new Error("Configured AgentBus MCP server was not found.");
    }
    return formatMcpServer(server);
  }
}

function parseServerUri(uri: vscode.Uri): string {
  const serverId = decodeURIComponent(uri.path.replace(/^\//, ""));
  if (uri.scheme !== "agentbus-mcp" || !isSafeControlId(serverId)) {
    throw new Error("Unsafe AgentBus MCP server document identity.");
  }
  return serverId;
}
