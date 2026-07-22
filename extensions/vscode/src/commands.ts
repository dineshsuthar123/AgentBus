import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import { formatApprovalConfirmation } from "./approvalPresentation";
import { toolArtifactUri } from "./artifactDocuments";
import { validateToolArtifact } from "./artifactPresentation";
import type { DaemonManager } from "./daemonManager";
import { changeUri, reportUri } from "./documents";
import { toolInvocationUri, toolPolicyUri } from "./toolDocuments";
import type {
  ApprovalSummary,
  CancelResponse,
  McpServerSummary,
  RunAcceptedResponse,
  RunCreateRequest,
  RunSummary,
  ToolInvocationSummary
} from "./generated/protocol";
import { ReconnectingSseClient } from "./sse";
import type { RunStore } from "./runStore";
import { safeError } from "./redaction";
import { mcpServerUri } from "./mcpDocuments";
import { formatMcpServerCheck } from "./mcpPresentation";
import { canonicalWorkspacePath, selectWorkspace } from "./workspace";
import type { AgentBusItem, RunSelection } from "./views";
import {
  canCancel,
  cancellationDetails,
  cancellationEventMessage,
  cancellationStatus
} from "./cancellation";
import {
  canCancelTool,
  toolCancellationDetail
} from "./toolPresentation";

export interface Refreshers {
  refresh(): void;
}

export class CommandController implements vscode.Disposable {
  private readonly disposables: vscode.Disposable[] = [];
  private readonly cancellationsInFlight = new Set<string>();
  private stream: ReconnectingSseClient | undefined;

  public constructor(
    private readonly daemon: DaemonManager,
    private readonly store: RunStore,
    private readonly selection: RunSelection,
    private readonly refreshers: Refreshers[],
    private readonly output: vscode.OutputChannel,
    private readonly status: vscode.StatusBarItem
  ) {}

  public register(context: vscode.ExtensionContext): void {
    const command = (name: string, callback: (...args: unknown[]) => unknown) =>
      this.disposables.push(vscode.commands.registerCommand(name, callback));
    command("agentbus.startTask", () => this.start(false, false));
    command("agentbus.startDurableTask", () => this.start(true, false));
    command("agentbus.startParallelTask", () => this.start(true, true));
    command("agentbus.submitRun", (request) =>
      this.submitRequest(request as RunCreateRequest)
    );
    command("agentbus.requestCancellation", (runId, reason) =>
      this.requestCancellation(String(runId), String(reason ?? ""))
    );
    command("agentbus.openChange", (runId, path) =>
      this.openChange(String(runId), String(path))
    );
    command("agentbus.refresh", () => this.refresh());
    command("agentbus.showRun", (item) => this.showRun(item));
    command("agentbus.resumeRun", () => this.resume());
    command("agentbus.cancelRun", () => this.cancel());
    command("agentbus.openRunReport", () => this.openReport());
    command("agentbus.openChanges", () => this.openChanges());
    command("agentbus.showToolInvocation", (item) =>
      this.showToolInvocation(item)
    );
    command("agentbus.cancelToolInvocation", (item) =>
      this.cancelToolInvocation(item)
    );
    command("agentbus.openToolArtifact", (item) =>
      this.openToolArtifact(item)
    );
    command("agentbus.showToolPolicy", () => this.showToolPolicy());
    command("agentbus.refreshToolRegistry", () => this.refreshToolRegistry());
    command("agentbus.showMcpServer", (item) => this.showMcpServer(item));
    command("agentbus.checkMcpServer", (item) => this.checkMcpServer(item));
    command("agentbus.approveAction", (item) => this.decide(item, "approve"));
    command("agentbus.rejectAction", (item) => this.decide(item, "reject"));
    command("agentbus.runDoctor", () => this.doctor());
    command("agentbus.selectProvider", () => this.selectProvider());
    command("agentbus.restartDaemon", () => this.restartDaemon());
    command("agentbus.stopDaemon", () => this.stopDaemon());
    command("agentbus.openLogs", () => this.output.show());
    context.subscriptions.push(...this.disposables);
    void this.connect();
  }

  public dispose(): void {
    this.stream?.stop();
    for (const item of this.disposables) item.dispose();
  }

  public eventStreamConnected(): boolean {
    return this.stream?.isConnected ?? false;
  }

  private async client(): Promise<AgentBusClient> {
    return (await this.daemon.connectOrStart()).client;
  }

  private async connect(): Promise<void> {
    try {
      const client = await this.client();
      this.stream?.stop();
      this.stream = new ReconnectingSseClient(
        client,
        (event) => {
          this.store.apply(event);
          const message = cancellationEventMessage(event);
          if (message && event.run_id) {
            this.output.appendLine(`[${event.run_id}] ${message}`);
          }
          this.refreshViews();
        },
        { onReconnectFailure: () => this.refresh() }
      );
      this.stream.start();
      await this.refresh();
    } catch (error) {
      this.output.appendLine(`AgentBus connection: ${safeError(error)}`);
      this.status.text = "$(debug-disconnect) AgentBus offline";
    }
  }

  private async refresh(): Promise<void> {
    const response = await (await this.client()).listRuns();
    this.store.replaceRuns(response.runs);
    this.refreshViews();
    const active = response.runs.filter((run) =>
      ["pending", "running", "waiting_for_approval", "waiting_for_review"].includes(
        run.status
      )
    ).length;
    this.status.text = active
      ? `$(sync~spin) AgentBus ${active}`
      : "$(check) AgentBus";
  }

  private refreshViews(): void {
    for (const provider of this.refreshers) provider.refresh();
  }

  private async start(durable: boolean, parallel: boolean): Promise<void> {
    const folder = await selectWorkspace(true);
    if (!folder) return;
    const task = await vscode.window.showInputBox({
      title: "AgentBus Task",
      prompt: "Describe the software engineering task",
      validateInput: (value) => value.trim() ? undefined : "Task is required."
    });
    if (!task) return;
    const workspace = await canonicalWorkspacePath(folder);
    const config = vscode.workspace.getConfiguration("agentbus");
    const provider = config.get<"ollama" | "azure" | "deterministic">(
      "defaultProvider",
      "ollama"
    );
    let consent = false;
    if (provider === "azure") {
      consent =
        (await vscode.window.showWarningMessage(
          "This task will make live Azure provider requests.",
          { modal: true },
          "Continue"
        )) === "Continue";
      if (!consent) return;
    }
    const workflow = parallel
      ? "multi"
      : config.get<"single" | "multi">("defaultWorkflow", "multi");
    const client = await this.client();
    const validation = await client.validateWorkspace({
      workspace,
      require_git: durable || parallel
    });
    if (!validation.valid) throw new Error(validation.message ?? "Invalid workspace.");
    await this.submitRequest({
      task,
      workspace: validation.workspace,
      provider,
      workflow,
      durable,
      parallel,
      max_workers: parallel ? config.get<number>("maxWorkers", 4) : 1,
      live_provider_consent: consent,
      keep_worktrees: config.get<boolean>("keepWorktrees", true)
    });
  }

  private async submitRequest(
    request: RunCreateRequest
  ): Promise<RunAcceptedResponse> {
    const accepted = await (await this.client()).createRun(request);
    this.store.upsert({
      run_id: accepted.run_id,
      status: accepted.status,
      workflow: String(request.workflow ?? "multi"),
      workspace: accepted.workspace,
      original_task: request.task,
      created_at: accepted.created_at,
      updated_at: accepted.created_at,
      version: 1
    });
    this.selection.set(accepted.run_id);
    this.status.text = "$(sync~spin) AgentBus 1";
    this.refreshViews();
    void vscode.window.showInformationMessage(`AgentBus run ${accepted.run_id} started.`);
    return accepted;
  }

  private async chooseRun(): Promise<RunSummary | undefined> {
    const selected = this.selection.get();
    if (selected) return this.store.run(selected);
    return vscode.window.showQuickPick(
      this.store.runs().map((run) => ({
        label: run.original_task,
        description: run.status,
        run
      }))
    ).then((item) => item?.run);
  }

  private async showRun(raw?: unknown): Promise<void> {
    const item = raw as AgentBusItem | undefined;
    const run = item?.value as RunSummary | undefined ?? await this.chooseRun();
    if (!run) return;
    this.selection.set(run.run_id);
    this.refreshViews();
    await vscode.commands.executeCommand("agentbus.openRunReport");
  }

  private async resume(): Promise<void> {
    const run = await this.chooseRun();
    if (!run) return;
    await (await this.client()).resume(run.run_id);
    await this.refresh();
  }

  private async cancel(): Promise<void> {
    const run = await this.chooseRun();
    if (!run) return;
    if (!canCancel(run) || this.cancellationsInFlight.has(run.run_id)) {
      const state =
        cancellationStatus(run.cancellation, run.status) ??
        `Run is already ${run.status}.`;
      this.output.appendLine(`[${run.run_id}] ${state}`);
      void vscode.window.showInformationMessage(state);
      return;
    }
    const confirmed = await vscode.window.showWarningMessage(
      `Cancel AgentBus run ${run.run_id}?`,
      { modal: true },
      "Cancel Run"
    );
    if (confirmed !== "Cancel Run") return;
    await this.requestCancellation(
      run.run_id,
      "Cancelled by local IDE user"
    );
  }

  private async requestCancellation(
    runId: string,
    reason: string
  ): Promise<CancelResponse> {
    let run = this.store.run(runId);
    if (!run) {
      run = await (await this.client()).run(runId);
      this.store.upsert(run);
    }
    if (!canCancel(run) || this.cancellationsInFlight.has(runId)) {
      const state =
        cancellationStatus(run.cancellation, run.status) ??
        `Run is already ${run.status}.`;
      this.output.appendLine(`[${runId}] ${state}`);
      return {
        run_id: runId,
        status: run.status,
        cancellation_requested: Boolean(run.cancellation?.requested),
        cancellation: run.cancellation
      };
    }
    this.cancellationsInFlight.add(runId);
    this.output.appendLine(`[${runId}] Cancelling...`);
    this.status.text = "$(sync~spin) AgentBus cancelling";
    try {
      const response = await (await this.client()).cancel(runId, reason || undefined);
      this.store.updateCancellation(
        runId,
        response.status,
        response.cancellation
      );
      for (const detail of cancellationDetails(
        response.cancellation,
        response.status
      )) {
        this.output.appendLine(`[${runId}] ${detail}`);
      }
      this.refreshViews();
      await this.refresh();
      return response;
    } finally {
      this.cancellationsInFlight.delete(runId);
    }
  }

  private async openReport(): Promise<void> {
    const run = await this.chooseRun();
    if (!run) return;
    const document = await vscode.workspace.openTextDocument(reportUri(run.run_id));
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async openChanges(): Promise<void> {
    const run = await this.chooseRun();
    if (!run) return;
    const changes = await (await this.client()).changes(run.run_id);
    const picked = await vscode.window.showQuickPick(
      changes.changes.map((change) => ({
        label: change.path,
        description: `${change.status}${change.generated ? " | generated" : ""}`,
        change
      }))
    );
    if (!picked) return;
    await this.openChange(run.run_id, picked.change.path);
  }

  private async showToolInvocation(raw?: unknown): Promise<void> {
    const item = raw as AgentBusItem | undefined;
    let invocation = item?.value as ToolInvocationSummary | undefined;
    if (!invocation) {
      const run = await this.chooseRun();
      if (!run) return;
      const response = await (await this.client()).toolInvocations(run.run_id);
      invocation = (await vscode.window.showQuickPick(
        response.invocations.map((value) => ({
          label: value.tool_name,
          description: `${value.status} | ${value.task_id}`,
          value
        }))
      ))?.value;
    }
    if (!invocation) return;
    const document = await vscode.workspace.openTextDocument(
      toolInvocationUri(invocation.run_id, invocation.invocation_id)
    );
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async cancelToolInvocation(raw?: unknown): Promise<void> {
    const item = raw as AgentBusItem | undefined;
    let invocation = item?.value as ToolInvocationSummary | undefined;
    if (!invocation) {
      const run = await this.chooseRun();
      if (!run) return;
      const response = await (await this.client()).toolInvocations(run.run_id);
      invocation = (await vscode.window.showQuickPick(
        response.invocations
          .filter((value) => canCancelTool(value.status))
          .map((value) => ({
            label: value.tool_name,
            description: `${value.status} | ${value.task_id}`,
            value
          }))
      ))?.value;
    }
    if (!invocation) return;
    const run = this.store.run(invocation.run_id);
    if (
      !canCancelTool(invocation.status) ||
      run?.cancellation?.requested ||
      this.cancellationsInFlight.has(invocation.run_id)
    ) {
      void vscode.window.showInformationMessage(
        "The owning run is already cancelling or this tool is terminal."
      );
      return;
    }
    const confirmed = await vscode.window.showWarningMessage(
      `Cancel ${invocation.tool_name}?`,
      { modal: true, detail: toolCancellationDetail(invocation) },
      "Cancel Owning Run"
    );
    if (confirmed !== "Cancel Owning Run") return;

    this.cancellationsInFlight.add(invocation.run_id);
    this.output.appendLine(
      `[${invocation.run_id}] Cancelling through tool ${invocation.invocation_id}...`
    );
    this.status.text = "$(sync~spin) AgentBus cancelling";
    try {
      const response = await (await this.client()).cancelToolInvocation(
        invocation.run_id,
        invocation.invocation_id,
        `Cancelled tool ${invocation.invocation_id} by local IDE user`
      );
      if (run) {
        this.store.updateCancellation(
          invocation.run_id,
          run.status,
          response.cancellation
        );
      }
      for (const detail of cancellationDetails(
        response.cancellation,
        run?.status
      )) {
        this.output.appendLine(`[${invocation.run_id}] ${detail}`);
      }
      this.refreshViews();
      await this.refresh();
    } finally {
      this.cancellationsInFlight.delete(invocation.run_id);
    }
  }

  private async openToolArtifact(raw?: unknown): Promise<void> {
    const item = raw as AgentBusItem | undefined;
    let invocation = item?.value as ToolInvocationSummary | undefined;
    if (!invocation) {
      const run = await this.chooseRun();
      if (!run) return;
      const response = await (await this.client()).toolInvocations(run.run_id);
      invocation = (await vscode.window.showQuickPick(
        response.invocations.map((value) => ({
          label: value.tool_name,
          description: `${value.status} | ${value.task_id}`,
          value
        }))
      ))?.value;
    }
    if (!invocation) return;
    const detail = await (await this.client()).toolInvocation(
      invocation.run_id,
      invocation.invocation_id
    );
    const openable = (detail.result?.artifacts ?? []).flatMap((artifact) => {
      try {
        return [validateToolArtifact(artifact)];
      } catch {
        return [];
      }
    });
    if (openable.length === 0) {
      void vscode.window.showInformationMessage(
        "This invocation has no safe UTF-8 repository file artifacts."
      );
      return;
    }
    const picked = await vscode.window.showQuickPick(
      openable.map((value) => ({
        label: value.path,
        description: `${value.mediaType} | ${value.artifact.size_bytes} bytes`,
        value
      })),
      { title: "Open Tool Artifact" }
    );
    if (!picked) return;
    const document = await vscode.workspace.openTextDocument(
      toolArtifactUri(
        invocation.run_id,
        invocation.invocation_id,
        picked.value.artifact.artifact_id
      )
    );
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async showToolPolicy(): Promise<void> {
    const document = await vscode.workspace.openTextDocument(toolPolicyUri());
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async refreshToolRegistry(): Promise<void> {
    const response = await (await this.client()).tools();
    this.refreshViews();
    void vscode.window.showInformationMessage(
      `AgentBus loaded ${response.total} managed tool descriptor(s).`
    );
  }

  private async showMcpServer(raw?: unknown): Promise<void> {
    const server = await this.chooseMcpServer(raw);
    if (!server) return;
    const document = await vscode.workspace.openTextDocument(
      mcpServerUri(server.server_id)
    );
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async checkMcpServer(raw?: unknown): Promise<void> {
    const requested = await this.chooseMcpServer(raw);
    if (!requested) return;
    const configured = await (await this.client()).mcpServers();
    const server = configured.servers.find(
      (candidate) => candidate.server_id === requested.server_id
    );
    if (!server) {
      throw new Error("MCP diagnostics are limited to configured servers.");
    }
    const response = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: `Checking local MCP server ${server.server_id}`
      },
      async () => (await this.client()).checkMcpServer(server.server_id)
    );
    const document = await vscode.workspace.openTextDocument({
      content: formatMcpServerCheck(response),
      language: "markdown"
    });
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async chooseMcpServer(
    raw?: unknown
  ): Promise<McpServerSummary | undefined> {
    const item = raw as AgentBusItem | undefined;
    const selected = item?.value as McpServerSummary | undefined;
    if (selected) return selected;
    const response = await (await this.client()).mcpServers();
    return (await vscode.window.showQuickPick(
      response.servers.map((server) => ({
        label: server.server_id,
        description: `${server.transport} | ${server.configured_tools.length} tool(s)`,
        server
      })),
      { title: "Select Configured MCP Server" }
    ))?.server;
  }

  private async openChange(runId: string, path: string): Promise<void> {
    await vscode.commands.executeCommand(
      "vscode.diff",
      changeUri({ runId, path, revision: "before" }),
      changeUri({ runId, path, revision: "after" }),
      `${path} (AgentBus)`
    );
  }

  private async decide(raw: unknown, decision: "approve" | "reject"): Promise<void> {
    const item = raw as AgentBusItem | undefined;
    let approval = item?.value as ApprovalSummary | undefined;
    if (!approval) {
      const run = await this.chooseRun();
      if (!run) return;
      const response = await (await this.client()).approvals(run.run_id);
      approval = (await vscode.window.showQuickPick(
        response.approvals.map((value) => ({
          label: value.requested_action,
          description: value.risk_category,
          value
        }))
      ))?.value;
    }
    if (!approval) return;
    if (approval.state !== "pending") {
      void vscode.window.showInformationMessage(
        `Approval is already ${approval.state}.`
      );
      return;
    }
    if (
      decision === "approve" &&
      (approval.approval_kind === "tool" || approval.risk_category === "high")
    ) {
      const confirmed = await vscode.window.showWarningMessage(
        approval.requested_action,
        {
          modal: true,
          detail:
            approval.approval_kind === "tool"
              ? formatApprovalConfirmation(approval)
              : approval.reason ?? undefined
        },
        "Approve"
      );
      if (confirmed !== "Approve") return;
    }
    const reason = await vscode.window.showInputBox({ prompt: `${decision} reason (optional)` });
    await (await this.client()).decideApproval(
      approval.run_id,
      approval.approval_id,
      decision,
      { revision: approval.revision ?? 1, reason: reason || undefined }
    );
    await this.refresh();
  }

  private async doctor(): Promise<void> {
    const folder = await selectWorkspace(false);
    const report = await (await this.client()).doctor(folder?.uri.fsPath);
    this.output.appendLine(JSON.stringify(report, null, 2));
    this.output.show();
  }

  private async selectProvider(): Promise<void> {
    const picked = await vscode.window.showQuickPick([
      "ollama",
      "azure",
      "deterministic"
    ]);
    if (picked) {
      await vscode.workspace.getConfiguration("agentbus").update(
        "defaultProvider",
        picked,
        vscode.ConfigurationTarget.Global
      );
    }
  }

  private async restartDaemon(): Promise<void> {
    this.stream?.stop();
    await this.daemon.restart();
    await this.connect();
  }

  private async stopDaemon(): Promise<void> {
    this.stream?.stop();
    await this.daemon.stop();
    this.status.text = "$(debug-disconnect) AgentBus offline";
  }
}
