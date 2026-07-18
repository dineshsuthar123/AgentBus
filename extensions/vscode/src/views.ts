import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import type {
  ApprovalSummary,
  ProviderSummary,
  RunSummary,
  TaskSummary,
  WorktreeSummary
} from "./generated/protocol";
import type { RunStore } from "./runStore";
import {
  canCancel,
  cancellationDetails,
  cancellationStatus
} from "./cancellation";

export interface RunSelection {
  get(): string | undefined;
  set(runId: string): void;
}

export class Selection implements RunSelection {
  private runId: string | undefined;

  public get(): string | undefined {
    return this.runId;
  }

  public set(runId: string): void {
    this.runId = runId;
  }
}

export class AgentBusItem extends vscode.TreeItem {
  public constructor(
    label: string,
    collapsibleState: vscode.TreeItemCollapsibleState,
    public readonly value?: unknown
  ) {
    super(label, collapsibleState);
  }
}

abstract class RefreshableProvider
  implements vscode.TreeDataProvider<AgentBusItem>
{
  protected readonly changed = new vscode.EventEmitter<
    AgentBusItem | undefined | void
  >();
  public readonly onDidChangeTreeData = this.changed.event;

  public refresh(): void {
    this.changed.fire();
  }

  public getTreeItem(element: AgentBusItem): vscode.TreeItem {
    return element;
  }

  public abstract getChildren(element?: AgentBusItem): Promise<AgentBusItem[]>;
}

export class RunsProvider extends RefreshableProvider {
  public constructor(private readonly store: RunStore) {
    super();
  }

  public async getChildren(element?: AgentBusItem): Promise<AgentBusItem[]> {
    if (element) {
      const category = String(element.value);
      return this.store
        .runs()
        .filter((run) => categoryFor(run.status) === category)
        .map(runItem);
    }
    const categories = [
      ["active", "Active"],
      ["approval", "Awaiting Approval"],
      ["failed", "Failed"],
      ["succeeded", "Succeeded"],
      ["cancelled", "Cancelled"]
    ] as const;
    return categories
      .filter(([key]) =>
        this.store.runs().some((run) => categoryFor(run.status) === key)
      )
      .map(([key, label]) => {
        const count = this.store
          .runs()
          .filter((run) => categoryFor(run.status) === key).length;
        const item = new AgentBusItem(
          `${label} (${count})`,
          vscode.TreeItemCollapsibleState.Expanded,
          key
        );
        item.contextValue = "agentbusRunCategory";
        return item;
      });
  }
}

export class TasksProvider extends RefreshableProvider {
  public constructor(
    private readonly client: () => Promise<AgentBusClient>,
    private readonly selection: RunSelection
  ) {
    super();
  }

  public async getChildren(): Promise<AgentBusItem[]> {
    const runId = this.selection.get();
    if (!runId) {
      return [messageItem("Select a run to inspect its task graph.")];
    }
    const response = await (await this.client()).tasks(runId);
    return response.tasks.map(taskItem);
  }
}

export class ApprovalsProvider extends RefreshableProvider {
  public constructor(
    private readonly client: () => Promise<AgentBusClient>,
    private readonly selection: RunSelection
  ) {
    super();
  }

  public async getChildren(): Promise<AgentBusItem[]> {
    const runId = this.selection.get();
    if (!runId) {
      return [messageItem("Select a run to inspect approvals.")];
    }
    const response = await (await this.client()).approvals(runId);
    return response.approvals.map(approvalItem);
  }
}

export class WorktreesProvider extends RefreshableProvider {
  public constructor(
    private readonly client: () => Promise<AgentBusClient>,
    private readonly selection: RunSelection
  ) {
    super();
  }

  public async getChildren(): Promise<AgentBusItem[]> {
    const runId = this.selection.get();
    if (!runId) {
      return [messageItem("Select a run to inspect worktrees.")];
    }
    const response = await (await this.client()).worktrees(runId);
    return response.worktrees.map(worktreeItem);
  }
}

export class ProvidersProvider extends RefreshableProvider {
  public constructor(private readonly client: () => Promise<AgentBusClient>) {
    super();
  }

  public async getChildren(): Promise<AgentBusItem[]> {
    const response = await (await this.client()).providers();
    return response.providers.map(providerItem);
  }
}

function runItem(run: RunSummary): AgentBusItem {
  const item = new AgentBusItem(
    run.original_task,
    vscode.TreeItemCollapsibleState.None,
    run
  );
  item.description =
    cancellationStatus(run.cancellation, run.status) ?? run.status;
  const cancellation = cancellationDetails(run.cancellation, run.status);
  item.tooltip = new vscode.MarkdownString(
    [
      `**${run.status}**`,
      ...cancellation.map((detail) => `\n\n${detail}`),
      `\n\nWorkspace: \`${run.workspace}\``,
      `\n\nRun: \`${run.run_id}\``
    ].join("")
  );
  item.iconPath = new vscode.ThemeIcon(iconForStatus(run.status));
  item.contextValue = canCancel(run)
    ? "agentbusRunCancellable"
    : run.cancellation?.requested
      ? "agentbusRunCancelling"
      : "agentbusRunTerminal";
  item.command = {
    command: "agentbus.showRun",
    title: "Show Run",
    arguments: [item]
  };
  return item;
}

function taskItem(task: TaskSummary): AgentBusItem {
  const item = new AgentBusItem(
    task.title,
    vscode.TreeItemCollapsibleState.None,
    task
  );
  item.description = `${task.status} | ${task.attempts} attempt(s)`;
  item.tooltip = new vscode.MarkdownString(
    [
      `**${task.status}**`,
      "",
      task.description,
      "",
      `Role: \`${task.assigned_role}\``,
      `Risk: \`${task.risk}\``,
      `Worker: \`${task.worker_id ?? "none"}\``,
      `Provider: \`${task.provider ?? "n/a"}\``,
      `Model: \`${task.model ?? "n/a"}\``
    ].join("\n")
  );
  item.iconPath = new vscode.ThemeIcon(iconForStatus(task.status));
  item.contextValue = "agentbusTask";
  return item;
}

function approvalItem(approval: ApprovalSummary): AgentBusItem {
  const item = new AgentBusItem(
    approval.requested_action,
    vscode.TreeItemCollapsibleState.None,
    approval
  );
  item.description = `${approval.risk_category} | ${approval.state}`;
  item.tooltip = new vscode.MarkdownString(
    `Reason: ${approval.reason ?? "not provided"}\n\nPaths: ${
      approval.affected_paths?.join(", ") || "none"
    }`
  );
  item.iconPath = new vscode.ThemeIcon("shield");
  item.contextValue = "agentbusApproval";
  return item;
}

function worktreeItem(worktree: WorktreeSummary): AgentBusItem {
  const item = new AgentBusItem(
    worktree.task_id ?? "Integration",
    vscode.TreeItemCollapsibleState.None,
    worktree
  );
  item.description = worktree.status;
  item.tooltip = worktree.path;
  item.iconPath = new vscode.ThemeIcon("git-branch");
  item.contextValue = "agentbusWorktree";
  item.resourceUri = vscode.Uri.file(worktree.path);
  return item;
}

function providerItem(provider: ProviderSummary): AgentBusItem {
  const item = new AgentBusItem(
    provider.name,
    vscode.TreeItemCollapsibleState.None,
    provider
  );
  item.description = provider.ready ? "ready" : "not ready";
  item.tooltip = provider.message ?? provider.model ?? "";
  item.iconPath = new vscode.ThemeIcon(
    provider.ready ? "pass-filled" : "warning"
  );
  item.contextValue = "agentbusProvider";
  return item;
}

function messageItem(message: string): AgentBusItem {
  const item = new AgentBusItem(
    message,
    vscode.TreeItemCollapsibleState.None
  );
  item.iconPath = new vscode.ThemeIcon("info");
  return item;
}

function categoryFor(status: string): string {
  if (status === "waiting_for_approval") {
    return "approval";
  }
  if (status === "failed") {
    return "failed";
  }
  if (status === "succeeded") {
    return "succeeded";
  }
  if (status === "cancelled") {
    return "cancelled";
  }
  return "active";
}

function iconForStatus(status: string): string {
  if (status === "succeeded") {
    return "pass-filled";
  }
  if (status === "failed") {
    return "error";
  }
  if (status === "cancelled") {
    return "circle-slash";
  }
  if (status.includes("approval")) {
    return "shield";
  }
  return "sync~spin";
}
