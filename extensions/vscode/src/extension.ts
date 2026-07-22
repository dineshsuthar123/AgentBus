import * as vscode from "vscode";
import { CommandController } from "./commands";
import type { AgentBusClient } from "./apiClient";
import { DaemonManager } from "./daemonManager";
import {
  ChangeDocumentProvider,
  ReportDocumentProvider
} from "./documents";
import { RunStore } from "./runStore";
import { ToolArtifactDocumentProvider } from "./artifactDocuments";
import { McpServerDocumentProvider } from "./mcpDocuments";
import {
  ToolInvocationDocumentProvider,
  ToolPolicyDocumentProvider
} from "./toolDocuments";
import type { EventEnvelope, RunSummary } from "./generated/protocol";
import {
  ApprovalsProvider,
  McpServersProvider,
  ProvidersProvider,
  RunsProvider,
  Selection,
  TasksProvider,
  ToolInvocationsProvider,
  WorktreesProvider
} from "./views";

export interface AgentBusExtensionApi {
  client(): Promise<AgentBusClient>;
  daemonId(): string | undefined;
  events(): readonly EventEnvelope[];
  runs(): RunSummary[];
}

export function activate(context: vscode.ExtensionContext): AgentBusExtensionApi {
  const output = vscode.window.createOutputChannel("AgentBus", { log: true });
  const status = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    50
  );
  status.command = "agentbus.showRun";
  status.text = "$(loading~spin) AgentBus";
  status.show();
  const daemon = new DaemonManager(context, output);
  const store = new RunStore();
  const selection = new Selection();
  const client = async () => (await daemon.connectOrStart()).client;
  const runs = new RunsProvider(store);
  const tasks = new TasksProvider(client, selection);
  const approvals = new ApprovalsProvider(client, selection);
  const worktrees = new WorktreesProvider(client, selection);
  const tools = new ToolInvocationsProvider(client, selection);
  const providers = new ProvidersProvider(client);
  const mcpServers = new McpServersProvider(client);
  context.subscriptions.push(
    output,
    status,
    daemon,
    vscode.window.registerTreeDataProvider("agentbus.runs", runs),
    vscode.window.registerTreeDataProvider("agentbus.tasks", tasks),
    vscode.window.registerTreeDataProvider("agentbus.approvals", approvals),
    vscode.window.registerTreeDataProvider("agentbus.worktrees", worktrees),
    vscode.window.registerTreeDataProvider("agentbus.tools", tools),
    vscode.window.registerTreeDataProvider("agentbus.providers", providers),
    vscode.window.registerTreeDataProvider("agentbus.mcp", mcpServers),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-before",
      new ChangeDocumentProvider(client)
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-after",
      new ChangeDocumentProvider(client)
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-report",
      new ReportDocumentProvider(client)
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-tool",
      new ToolInvocationDocumentProvider(client)
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-policy",
      new ToolPolicyDocumentProvider(client)
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-artifact",
      new ToolArtifactDocumentProvider(client)
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-mcp",
      new McpServerDocumentProvider(client)
    )
  );
  const controller = new CommandController(
    daemon,
    store,
    selection,
    [runs, tasks, approvals, worktrees, tools, providers, mcpServers],
    output,
    status
  );
  controller.register(context);
  context.subscriptions.push(controller);
  return {
    client,
    daemonId: () => daemon.current()?.entry.daemon_id,
    events: () => store.events(),
    runs: () => store.runs()
  };
}

export function deactivate(): void {}
