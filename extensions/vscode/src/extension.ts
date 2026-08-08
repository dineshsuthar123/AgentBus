import * as vscode from "vscode";
import { CommandController } from "./commands";
import {
  ComparisonDocumentProvider,
  ComparisonSideDocumentProvider
} from "./comparisonDocuments";
import { ComparisonStore } from "./comparisonStore";
import type { AgentBusClient } from "./apiClient";
import { DaemonManager } from "./daemonManager";
import { IntelligenceCommandController } from "./intelligenceCommands";
import { IntelligenceDocumentProvider } from "./intelligenceDocuments";
import { RepositoryIntelligenceState } from "./intelligenceState";
import {
  ContextPlanProvider,
  ImpactAnalysisProvider,
  RepositoryIntelligenceProvider,
  SymbolExplorerProvider
} from "./intelligenceViews";
import {
  ChangeDocumentProvider,
  ReportDocumentProvider
} from "./documents";
import { RunStore } from "./runStore";
import { ReplayDocumentProvider } from "./replayDocuments";
import { ReplayPlanDocumentProvider } from "./replayPlanDocuments";
import { ProvenanceDocumentProvider } from "./provenanceDocuments";
import { ToolArtifactDocumentProvider } from "./artifactDocuments";
import { McpServerDocumentProvider } from "./mcpDocuments";
import { SpanDocumentProvider } from "./traceDocuments";
import {
  ToolInvocationDocumentProvider,
  ToolPolicyDocumentProvider
} from "./toolDocuments";
import type { EventEnvelope, RunSummary } from "./generated/protocol";
import {
  ApprovalsProvider,
  ComparisonsProvider,
  ExecutionTimelineProvider,
  McpServersProvider,
  ProvidersProvider,
  ReplaySessionsProvider,
  RunsProvider,
  Selection,
  TasksProvider,
  ToolInvocationsProvider,
  WorktreesProvider
} from "./views";

export interface AgentBusExtensionApi {
  client(): Promise<AgentBusClient>;
  daemonId(): string | undefined;
  eventStreamConnected(): boolean;
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
  const comparisonStore = new ComparisonStore(context.workspaceState);
  const selection = new Selection();
  const client = async () => (await daemon.connectOrStart()).client;
  const runs = new RunsProvider(store);
  const tasks = new TasksProvider(client, selection);
  const timeline = new ExecutionTimelineProvider(client, selection);
  const approvals = new ApprovalsProvider(client, selection);
  const worktrees = new WorktreesProvider(client, selection);
  const tools = new ToolInvocationsProvider(client, selection);
  const providers = new ProvidersProvider(client);
  const replays = new ReplaySessionsProvider(client);
  const comparisons = new ComparisonsProvider(client, comparisonStore);
  const mcpServers = new McpServersProvider(client);
  const intelligenceState = new RepositoryIntelligenceState(client);
  const intelligenceDocuments = new IntelligenceDocumentProvider();
  const repositoryIntelligence = new RepositoryIntelligenceProvider(
    intelligenceState
  );
  const symbolExplorer = new SymbolExplorerProvider(intelligenceState);
  const impactAnalysis = new ImpactAnalysisProvider(intelligenceState);
  const contextPlan = new ContextPlanProvider(intelligenceState);
  context.subscriptions.push(
    output,
    status,
    daemon,
    vscode.window.registerTreeDataProvider("agentbus.runs", runs),
    vscode.window.registerTreeDataProvider("agentbus.tasks", tasks),
    vscode.window.registerTreeDataProvider("agentbus.timeline", timeline),
    vscode.window.registerTreeDataProvider("agentbus.approvals", approvals),
    vscode.window.registerTreeDataProvider("agentbus.worktrees", worktrees),
    vscode.window.registerTreeDataProvider("agentbus.tools", tools),
    vscode.window.registerTreeDataProvider("agentbus.providers", providers),
    vscode.window.registerTreeDataProvider("agentbus.replays", replays),
    vscode.window.registerTreeDataProvider(
      "agentbus.comparisons",
      comparisons
    ),
    vscode.window.registerTreeDataProvider("agentbus.mcp", mcpServers),
    vscode.window.registerTreeDataProvider(
      "agentbus.intelligence",
      repositoryIntelligence
    ),
    vscode.window.registerTreeDataProvider("agentbus.symbols", symbolExplorer),
    vscode.window.registerTreeDataProvider("agentbus.impact", impactAnalysis),
    vscode.window.registerTreeDataProvider("agentbus.contextPlan", contextPlan),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-intelligence",
      intelligenceDocuments
    ),
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
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-span",
      new SpanDocumentProvider(client)
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-replay",
      new ReplayDocumentProvider(client)
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-replay-plan",
      new ReplayPlanDocumentProvider(client)
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-provenance",
      new ProvenanceDocumentProvider(client)
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-comparison",
      new ComparisonDocumentProvider(client)
    ),
    vscode.workspace.registerTextDocumentContentProvider(
      "agentbus-comparison-side",
      new ComparisonSideDocumentProvider(client)
    ),
    intelligenceState,
    intelligenceDocuments,
    repositoryIntelligence,
    symbolExplorer,
    impactAnalysis,
    contextPlan
  );
  const controller = new CommandController(
    daemon,
    store,
    selection,
    comparisonStore,
    [
      runs,
      tasks,
      timeline,
      approvals,
      worktrees,
      tools,
      providers,
      replays,
      comparisons,
      mcpServers,
      intelligenceState
    ],
    output,
    status
  );
  controller.register(context);
  context.subscriptions.push(controller);
  const intelligenceController = new IntelligenceCommandController(
    intelligenceState,
    intelligenceDocuments,
    output
  );
  intelligenceController.register(context);
  context.subscriptions.push(intelligenceController);
  return {
    client,
    daemonId: () => daemon.current()?.entry.daemon_id,
    eventStreamConnected: () => controller.eventStreamConnected(),
    events: () => store.events(),
    runs: () => store.runs()
  };
}

export function deactivate(): void {}
