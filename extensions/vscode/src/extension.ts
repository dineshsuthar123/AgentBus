import * as vscode from "vscode";
import { CommandController } from "./commands";
import type { AgentBusClient } from "./apiClient";
import { DaemonManager } from "./daemonManager";
import {
  ChangeDocumentProvider,
  ReportDocumentProvider
} from "./documents";
import { RunStore } from "./runStore";
import type { EventEnvelope, RunSummary } from "./generated/protocol";
import {
  ApprovalsProvider,
  ProvidersProvider,
  RunsProvider,
  Selection,
  TasksProvider,
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
  const providers = new ProvidersProvider(client);
  context.subscriptions.push(
    output,
    status,
    daemon,
    vscode.window.registerTreeDataProvider("agentbus.runs", runs),
    vscode.window.registerTreeDataProvider("agentbus.tasks", tasks),
    vscode.window.registerTreeDataProvider("agentbus.approvals", approvals),
    vscode.window.registerTreeDataProvider("agentbus.worktrees", worktrees),
    vscode.window.registerTreeDataProvider("agentbus.providers", providers),
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
    )
  );
  const controller = new CommandController(
    daemon,
    store,
    selection,
    [runs, tasks, approvals, worktrees, providers],
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
