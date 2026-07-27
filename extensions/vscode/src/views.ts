import * as vscode from "vscode";
import { AgentBusApiError, type AgentBusClient } from "./apiClient";
import type {
  ApprovalSummary,
  McpServerSummary,
  ProviderSummary,
  ReplaySessionResponse,
  RunSummary,
  TaskSummary,
  TraceSpanSummary,
  ToolInvocationSummary,
  WorktreeSummary
} from "./generated/protocol";
import type { RunStore } from "./runStore";
import {
  REPLAY_GROUPS,
  replayDescription,
  replayGroup,
  replayTooltip,
  type ReplayGroupKey
} from "./replayPresentation";
import {
  spanDescription,
  spanTooltip,
  timelineChildren
} from "./tracePresentation";
import { formatApprovalTooltip } from "./approvalPresentation";
import { formatMcpServerTooltip } from "./mcpPresentation";
import {
  canCancel,
  cancellationDetails,
  cancellationStatus
} from "./cancellation";
import {
  TOOL_GROUPS,
  canCancelTool,
  capabilityNames,
  escapeMarkdown,
  toolDuration,
  toolGroup,
  toolResourceSummary,
  toolVersion,
  type ToolGroupKey
} from "./toolPresentation";

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

export class ExecutionTimelineProvider extends RefreshableProvider {
  private cachedRunId: string | undefined;
  private cachedSpans: TraceSpanSummary[] | undefined;
  private cachedTruncated = false;

  public constructor(
    private readonly client: () => Promise<AgentBusClient>,
    private readonly selection: RunSelection
  ) {
    super();
  }

  public override refresh(): void {
    this.cachedRunId = undefined;
    this.cachedSpans = undefined;
    this.cachedTruncated = false;
    super.refresh();
  }

  public async getChildren(element?: AgentBusItem): Promise<AgentBusItem[]> {
    const runId = this.selection.get();
    if (!runId) {
      return [messageItem("Select a run to inspect its execution timeline.")];
    }
    const spans = await this.load(runId);
    if (!spans) {
      return [messageItem("This run does not have a recorded trace yet.")];
    }
    const parent = element?.value as TraceSpanSummary | undefined;
    const children = timelineChildren(spans, parent?.span_id).map((span) =>
      traceSpanItem(span, spans)
    );
    if (!element && this.cachedTruncated) {
      children.push(messageItem("Showing the first 500 trace spans."));
    }
    if (!element && children.length === 0) {
      return [messageItem("The recorded trace does not contain spans.")];
    }
    return children;
  }

  private async load(runId: string): Promise<TraceSpanSummary[] | undefined> {
    if (this.cachedRunId === runId && this.cachedSpans) {
      return this.cachedSpans;
    }
    try {
      const response = await (await this.client()).traceSpans(runId, 0, 500);
      this.cachedRunId = runId;
      this.cachedSpans = response.spans;
      this.cachedTruncated = response.truncated ?? false;
      return this.cachedSpans;
    } catch (error) {
      if (error instanceof AgentBusApiError && error.status === 404) {
        this.cachedRunId = runId;
        this.cachedSpans = [];
        return undefined;
      }
      throw error;
    }
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

interface ToolGroupValue {
  kind: "tool-group";
  key: ToolGroupKey;
}

export class ToolInvocationsProvider extends RefreshableProvider {
  private cachedRunId: string | undefined;
  private cachedInvocations: ToolInvocationSummary[] | undefined;
  private cachedApprovalStates = new Map<string, string>();
  private cachedTruncated = false;

  public constructor(
    private readonly client: () => Promise<AgentBusClient>,
    private readonly selection: RunSelection
  ) {
    super();
  }

  public override refresh(): void {
    this.cachedRunId = undefined;
    this.cachedInvocations = undefined;
    this.cachedApprovalStates.clear();
    this.cachedTruncated = false;
    super.refresh();
  }

  public async getChildren(element?: AgentBusItem): Promise<AgentBusItem[]> {
    const runId = this.selection.get();
    if (!runId) {
      return [messageItem("Select a run to inspect managed tool calls.")];
    }
    const invocations = await this.load(runId);
    if (element) {
      const group = element.value as ToolGroupValue;
      if (group?.kind !== "tool-group") return [];
      return invocations
        .filter((invocation) => toolGroup(invocation.status) === group.key)
        .map((invocation) =>
          toolInvocationItem(
            invocation,
            invocation.approval_id
              ? this.cachedApprovalStates.get(invocation.approval_id)
              : undefined
          )
        );
    }
    const groups = TOOL_GROUPS.map(({ key, label }) => {
      const count = invocations.filter(
        (invocation) => toolGroup(invocation.status) === key
      ).length;
      const item = new AgentBusItem(
        `${label} (${count})`,
        key === "active" || key === "awaiting_approval"
          ? vscode.TreeItemCollapsibleState.Expanded
          : vscode.TreeItemCollapsibleState.Collapsed,
        { kind: "tool-group", key } satisfies ToolGroupValue
      );
      item.contextValue = "agentbusToolGroup";
      item.iconPath = new vscode.ThemeIcon(iconForStatus(key));
      return item;
    });
    if (this.cachedTruncated) {
      groups.push(messageItem("Showing the first 500 managed tool calls."));
    }
    return groups;
  }

  private async load(runId: string): Promise<ToolInvocationSummary[]> {
    if (this.cachedRunId !== runId || !this.cachedInvocations) {
      const client = await this.client();
      const [response, approvals] = await Promise.all([
        client.toolInvocations(runId),
        client.approvals(runId)
      ]);
      this.cachedRunId = runId;
      this.cachedInvocations = response.invocations;
      this.cachedTruncated = response.truncated ?? false;
      this.cachedApprovalStates = new Map(
        approvals.approvals.map((approval) => [
          approval.approval_id,
          approval.state
        ])
      );
    }
    return this.cachedInvocations;
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

interface ReplayGroupValue {
  kind: "replay-group";
  key: ReplayGroupKey;
}

export class ReplaySessionsProvider extends RefreshableProvider {
  private cached: ReplaySessionResponse[] | undefined;
  private truncated = false;

  public constructor(private readonly client: () => Promise<AgentBusClient>) {
    super();
  }

  public override refresh(): void {
    this.cached = undefined;
    this.truncated = false;
    super.refresh();
  }

  public async getChildren(element?: AgentBusItem): Promise<AgentBusItem[]> {
    const sessions = await this.load();
    if (element) {
      const group = element.value as ReplayGroupValue;
      if (group?.kind !== "replay-group") return [];
      return sessions
        .filter((session) => replayGroup(session.status) === group.key)
        .map(replaySessionItem);
    }
    const groups = REPLAY_GROUPS.map(({ key, label }) => {
      const count = sessions.filter(
        (session) => replayGroup(session.status) === key
      ).length;
      const item = new AgentBusItem(
        `${label} (${count})`,
        key === "active"
          ? vscode.TreeItemCollapsibleState.Expanded
          : vscode.TreeItemCollapsibleState.Collapsed,
        { kind: "replay-group", key } satisfies ReplayGroupValue
      );
      item.contextValue = "agentbusReplayGroup";
      item.iconPath = new vscode.ThemeIcon(iconForStatus(key));
      return item;
    });
    if (this.truncated) {
      groups.push(messageItem("Showing the first 500 replay sessions."));
    }
    return groups;
  }

  private async load(): Promise<ReplaySessionResponse[]> {
    if (!this.cached) {
      const response = await (await this.client()).listReplays(
        undefined,
        undefined,
        500
      );
      this.cached = response.replays;
      this.truncated = response.truncated ?? false;
    }
    return this.cached;
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

function traceSpanItem(
  span: TraceSpanSummary,
  spans: readonly TraceSpanSummary[]
): AgentBusItem {
  const hasChildren = timelineChildren(spans, span.span_id).length > 0;
  const item = new AgentBusItem(
    span.name,
    hasChildren
      ? vscode.TreeItemCollapsibleState.Collapsed
      : vscode.TreeItemCollapsibleState.None,
    span
  );
  item.description = spanDescription(span);
  item.tooltip = new vscode.MarkdownString(spanTooltip(span));
  item.iconPath = new vscode.ThemeIcon(iconForTraceSpan(span));
  item.contextValue = "agentbusTraceSpan";
  item.command = {
    command: "agentbus.showSpan",
    title: "Show Span",
    arguments: [item]
  };
  return item;
}

function approvalItem(approval: ApprovalSummary): AgentBusItem {
  const item = new AgentBusItem(
    approval.requested_action,
    vscode.TreeItemCollapsibleState.None,
    approval
  );
  item.description = `${approval.risk_category} | ${approval.state}`;
  item.tooltip = new vscode.MarkdownString(formatApprovalTooltip(approval));
  item.iconPath = new vscode.ThemeIcon("shield");
  item.contextValue =
    approval.state === "pending"
      ? "agentbusApprovalPending"
      : "agentbusApprovalTerminal";
  return item;
}

export class McpServersProvider extends RefreshableProvider {
  public constructor(private readonly client: () => Promise<AgentBusClient>) {
    super();
  }

  public async getChildren(): Promise<AgentBusItem[]> {
    const response = await (await this.client()).mcpServers();
    if (response.servers.length === 0) {
      return [messageItem("No local MCP servers are configured.")];
    }
    return response.servers.map(mcpServerItem);
  }
}

function toolInvocationItem(
  invocation: ToolInvocationSummary,
  approvalState?: string
): AgentBusItem {
  const item = new AgentBusItem(
    invocation.tool_name,
    vscode.TreeItemCollapsibleState.None,
    invocation
  );
  item.description = `${invocation.status} | ${invocation.task_id}`;
  const decision = invocation.policy_decision;
  const cancellation = invocation.cancellation;
  item.tooltip = new vscode.MarkdownString(
    [
      `**${escapeMarkdown(invocation.tool_name)}** v${toolVersion(invocation)}`,
      "",
      `Status: \`${escapeMarkdown(invocation.status)}\``,
      `Task: \`${escapeMarkdown(invocation.task_id)}\``,
      `Caller: \`${escapeMarkdown(invocation.caller_role)}\``,
      `Capabilities: ${escapeMarkdown(capabilityNames(invocation.capabilities))}`,
      `Policy: ${escapeMarkdown(decision?.outcome ?? "not evaluated")}`,
      `Rule: \`${escapeMarkdown(decision?.rule_id ?? "n/a")}\``,
      `Policy reason: ${escapeMarkdown(decision?.reason ?? "n/a")}`,
      `Approval: \`${escapeMarkdown(invocation.approval_id ?? "none")}\` (${escapeMarkdown(approvalState ?? (invocation.approval_id ? "unknown" : "not required"))})`,
      `Duration: ${toolDuration(invocation)}`,
      `Resources: ${escapeMarkdown(toolResourceSummary(invocation))}`,
      `Timeout: ${invocation.status === "timed_out" ? "yes" : "no"}`,
      `Cancellation: ${cancellation.requested ? "requested" : "not requested"}`
    ].join("\n\n")
  );
  item.iconPath = new vscode.ThemeIcon(iconForStatus(invocation.status));
  item.contextValue = canCancelTool(invocation.status)
    ? "agentbusToolCancellable"
    : "agentbusToolTerminal";
  item.command = {
    command: "agentbus.showToolInvocation",
    title: "Show Tool Invocation",
    arguments: [item]
  };
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

function replaySessionItem(session: ReplaySessionResponse): AgentBusItem {
  const item = new AgentBusItem(
    session.replay_id,
    vscode.TreeItemCollapsibleState.None,
    session
  );
  item.description = replayDescription(session);
  item.tooltip = new vscode.MarkdownString(replayTooltip(session));
  item.iconPath = new vscode.ThemeIcon(iconForStatus(session.status));
  item.contextValue = ["pending", "running"].includes(session.status)
    ? "agentbusReplayCancellable"
    : "agentbusReplayTerminal";
  item.command = {
    command: "agentbus.showReplaySession",
    title: "Show Replay Session",
    arguments: [item]
  };
  return item;
}

function mcpServerItem(server: McpServerSummary): AgentBusItem {
  const item = new AgentBusItem(
    server.server_id,
    vscode.TreeItemCollapsibleState.None,
    server
  );
  item.description = `${server.transport} | ${server.configured_tools.length} tool(s)`;
  item.tooltip = new vscode.MarkdownString(formatMcpServerTooltip(server));
  item.iconPath = new vscode.ThemeIcon("server-process");
  item.contextValue = "agentbusMcpServer";
  item.command = {
    command: "agentbus.showMcpServer",
    title: "Show MCP Server",
    arguments: [item]
  };
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
  if (status === "denied") {
    return "lock";
  }
  if (status === "timed_out") {
    return "watch";
  }
  if (status.includes("approval")) {
    return "shield";
  }
  return "sync~spin";
}

function iconForTraceSpan(span: TraceSpanSummary): string {
  if (span.status === "failed") return "error";
  if (span.status === "cancelled" || span.status === "interrupted") {
    return "circle-slash";
  }
  if (span.status === "succeeded") {
    if (span.span_type === "tool_invocation") return "tools";
    if (span.span_type === "approval_wait") return "shield";
    if (span.span_type === "provider_request") return "cloud-upload";
    if (span.span_type === "provider_response") return "cloud-download";
    if (span.span_type === "verifier") return "verified";
    if (span.span_type === "reviewer") return "comment-discussion";
    if (span.span_type === "integration") return "git-merge";
    if (span.span_type === "cleanup") return "trash";
    return "pass-filled";
  }
  return "sync~spin";
}
