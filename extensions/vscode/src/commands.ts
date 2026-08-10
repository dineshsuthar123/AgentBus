import * as vscode from "vscode";
import { AgentBusApiError, type AgentBusClient } from "./apiClient";
import { formatApprovalConfirmation } from "./approvalPresentation";
import { toolArtifactUri } from "./artifactDocuments";
import { validateToolArtifact } from "./artifactPresentation";
import type { DaemonManager } from "./daemonManager";
import { changeUri, reportUri } from "./documents";
import {
  comparisonSideUri,
  comparisonUri
} from "./comparisonDocuments";
import type { ComparisonStore } from "./comparisonStore";
import { toolInvocationUri, toolPolicyUri } from "./toolDocuments";
import type {
  ApprovalSummary,
  CancelResponse,
  ComparisonResponse,
  McpServerSummary,
  RegressionFixtureCaptureResponse,
  ReplayAcceptedResponse,
  ReplayCancelResponse,
  ReplayCreateRequest,
  ReplaySessionResponse,
  RunAcceptedResponse,
  RunCreateRequest,
  RunSummary,
  TraceArchiveExportResponse,
  TraceArchiveImportResponse,
  TraceSpanSummary,
  ToolInvocationSummary
} from "./generated/protocol";
import { ReconnectingSseClient } from "./sse";
import type { RunStore } from "./runStore";
import { safeError } from "./redaction";
import { mcpServerUri } from "./mcpDocuments";
import { formatMcpServerCheck } from "./mcpPresentation";
import { replayUri } from "./replayDocuments";
import { replayPlanUri } from "./replayPlanDocuments";
import { provenanceUri } from "./provenanceDocuments";
import {
  FORK_INPUT_NAMES,
  isTerminalReplayStatus,
  offlineReplayBlockReason,
  parseForkInput,
  replayableCheckpoints,
  validateForkInputs,
  type ForkInputName,
  type ValidatedForkInputs
} from "./replayPresentation";
import { spanUri } from "./traceDocuments";
import {
  archiveSha256,
  decodeRegressionFixtureArchive,
  decodeTraceArchive,
  encodeTraceArchive,
  TRACE_ARCHIVE_EXTENSION,
  validateTraceArchiveFileName
} from "./traceArchive";
import {
  canonicalWorkspacePath,
  ensureWorkspaceTrust,
  selectWorkspace
} from "./workspace";
import type { AgentBusItem, RunSelection } from "./views";
import {
  canCancel,
  cancellationDetails,
  cancellationEventMessage,
  cancellationStatus
} from "./cancellation";
import {
  canCancelTool,
  isSafeControlId,
  toolCancellationDetail
} from "./toolPresentation";

export interface Refreshers {
  refresh(): void;
}

export class CommandController implements vscode.Disposable {
  private readonly disposables: vscode.Disposable[] = [];
  private readonly cancellationsInFlight = new Set<string>();
  private readonly replayMonitors = new Map<string, AbortController>();
  private stream: ReconnectingSseClient | undefined;

  public constructor(
    private readonly daemon: DaemonManager,
    private readonly store: RunStore,
    private readonly selection: RunSelection,
    private readonly comparisons: ComparisonStore,
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
    command("agentbus.showExecutionTimeline", () => this.showTimeline());
    command("agentbus.showSpan", (item) => this.showSpan(item));
    command("agentbus.showReplaySession", (item) =>
      this.showReplaySession(item)
    );
    command("agentbus.compareRuns", (left, right) =>
      this.compareRuns(left, right)
    );
    command("agentbus.showComparison", (item) =>
      this.showComparison(item)
    );
    command("agentbus.openStructuredReplayDifferences", (item) =>
      this.openStructuredReplayDifferences(item)
    );
    command("agentbus.compareRunReports", (left, right) =>
      this.compareRunReports(left, right)
    );
    command("agentbus.replayRunOffline", (run) =>
      this.replayRunOffline(run)
    );
    command("agentbus.replayFromCheckpoint", (request) =>
      this.replayFromCheckpoint(request)
    );
    command("agentbus.forkRun", (request) => this.forkRun(request));
    command("agentbus.exportTrace", (request) =>
      this.exportTrace(request)
    );
    command("agentbus.importTrace", (request) =>
      this.importTrace(request)
    );
    command("agentbus.captureRegressionFixture", (request) =>
      this.captureRegressionFixture(request)
    );
    command("agentbus.openProvenanceManifest", (run) =>
      this.openProvenanceManifest(run)
    );
    command("agentbus.cancelReplay", (replay) =>
      this.cancelReplay(replay)
    );
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
    if (vscode.workspace.isTrusted) {
      void this.connect();
    } else {
      this.status.text = "$(shield) AgentBus restricted";
      this.output.appendLine(
        "AgentBus daemon startup is paused until this workspace is trusted."
      );
    }
    this.disposables.push(
      vscode.workspace.onDidGrantWorkspaceTrust(() => {
        void this.connect();
      })
    );
  }

  public dispose(): void {
    this.stream?.stop();
    for (const controller of this.replayMonitors.values()) {
      controller.abort();
    }
    this.replayMonitors.clear();
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

  private async showTimeline(): Promise<void> {
    const run = await this.chooseRun();
    if (!run) return;
    this.selection.set(run.run_id);
    this.refreshViews();
    await vscode.commands.executeCommand("agentbus.timeline.focus");
  }

  private async showSpan(raw?: unknown): Promise<void> {
    const item = raw as AgentBusItem | undefined;
    let span = item?.value as TraceSpanSummary | undefined;
    let runId = span?.run_id;
    if (!span) {
      const run = await this.chooseRun();
      if (!run) return;
      runId = run.run_id;
      const response = await (await this.client()).traceSpans(runId, 0, 500);
      span = (
        await vscode.window.showQuickPick(
          response.spans.map((value) => ({
            label: value.name,
            description: `#${value.sequence} ${value.span_type} | ${value.status}`,
            value
          })),
          { title: "Show AgentBus Span" }
        )
      )?.value;
    }
    if (!span || !runId) return;
    const document = await vscode.workspace.openTextDocument(
      spanUri(runId, span.span_id)
    );
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async showReplaySession(raw?: unknown): Promise<void> {
    const item = raw as AgentBusItem | undefined;
    let replay = item?.value as ReplaySessionResponse | undefined;
    if (!replay) {
      const response = await (await this.client()).listReplays(
        undefined,
        undefined,
        500
      );
      replay = (
        await vscode.window.showQuickPick(
          response.replays.map((value) => ({
            label: value.replay_id,
            description: `${value.mode} | ${value.status}`,
            value
          })),
          { title: "Show AgentBus Replay Session" }
        )
      )?.value;
    }
    if (!replay) return;
    const document = await vscode.workspace.openTextDocument(
      replayUri(replay.replay_id)
    );
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async replayRunOffline(
    raw?: unknown
  ): Promise<ReplayAcceptedResponse | undefined> {
    const run = await this.resolveRun(raw);
    if (!run) return undefined;
    if (!(await this.prepareOfflineReplay(run.run_id))) return undefined;
    return this.submitReplay(run.run_id, {
      mode: "offline",
      live_provider_consent: false
    });
  }

  private async replayFromCheckpoint(
    raw?: unknown
  ): Promise<ReplayAcceptedResponse | undefined> {
    const request = commandRecord(raw);
    const run = await this.resolveRun(request?.runId ?? raw);
    if (!run) return undefined;
    const trace = await (await this.client()).trace(run.run_id);
    const checkpoints = replayableCheckpoints(trace.checkpoints ?? []);
    if (checkpoints.length === 0) {
      void vscode.window.showInformationMessage(
        "This trace has no replayable checkpoints."
      );
      return undefined;
    }
    const requestedId =
      typeof request?.checkpointId === "string"
        ? request.checkpointId
        : undefined;
    let checkpoint = requestedId
      ? checkpoints.find((value) => value.checkpoint_id === requestedId)
      : undefined;
    if (requestedId && !checkpoint) {
      throw new Error(
        "The requested checkpoint is not replayable for this trace."
      );
    }
    checkpoint ??= (
      await vscode.window.showQuickPick(
        checkpoints.map((value) => ({
          label: value.label,
          description: `#${value.sequence} | ${value.checkpoint_id}`,
          value
        })),
        { title: "Replay AgentBus from Checkpoint" }
      )
    )?.value;
    if (!checkpoint) return undefined;
    if (!(await this.prepareOfflineReplay(run.run_id))) return undefined;
    void vscode.window.showInformationMessage(
      `Replay will start from checkpoint ${checkpoint.label} (${checkpoint.checkpoint_id}).`
    );
    return this.submitReplay(run.run_id, {
      mode: "offline",
      from_checkpoint_id: checkpoint.checkpoint_id,
      live_provider_consent: false
    });
  }

  private async forkRun(
    raw?: unknown
  ): Promise<ReplayAcceptedResponse | undefined> {
    const request = commandRecord(raw);
    const run = await this.resolveRun(request?.runId ?? raw);
    if (!run) return undefined;
    let inputs: ValidatedForkInputs;
    if (isRecord(request?.changedInputs)) {
      inputs = validateForkInputs(request.changedInputs);
    } else {
      const name = await vscode.window.showQuickPick(
        FORK_INPUT_NAMES.map((value) => ({
          label: value,
          value
        })),
        { title: "Select Fork Input" }
      );
      if (!name) return undefined;
      const json = await vscode.window.showInputBox({
        title: `Fork Input: ${name.value}`,
        prompt: "Enter the replacement value as JSON",
        value: "null",
        validateInput: (value) => {
          try {
            parseForkInput(name.value as ForkInputName, value);
            return undefined;
          } catch (error) {
            return safeError(error);
          }
        }
      });
      if (json === undefined) return undefined;
      inputs = parseForkInput(name.value as ForkInputName, json);
    }
    if (inputs.liveProviderRequested) {
      void vscode.window.showErrorMessage(
        "VS Code fork replay does not enable live Azure or Ollama routes. No provider request was sent."
      );
      return undefined;
    }
    if (!(await this.prepareOfflineReplay(run.run_id))) return undefined;
    void vscode.window.showInformationMessage(
      `Fork replay will change: ${inputs.changedInputNames.join(", ")}. Changed values are not displayed.`
    );
    return this.submitReplay(run.run_id, {
      mode: "offline",
      fork: true,
      changed_inputs: inputs.changedInputs,
      live_provider_consent: false
    });
  }

  private async exportTrace(
    raw?: unknown
  ): Promise<TraceArchiveExportResponse | undefined> {
    const request = commandRecord(raw);
    const run = await this.resolveRun(request?.runId ?? raw);
    if (!run) return undefined;
    let includeSourceContent =
      typeof request?.includeSourceContent === "boolean"
        ? request.includeSourceContent
        : undefined;
    if (includeSourceContent === undefined) {
      includeSourceContent =
        (
          await vscode.window.showQuickPick(
            [
              {
                label: "Sanitized archive",
                description: "Recommended; excludes source-content objects.",
                value: false
              },
              {
                label: "Include source content",
                description: "Requires explicit consent and careful handling.",
                value: true
              }
            ],
            { title: "Export AgentBus Trace" }
          )
        )?.value;
    }
    if (includeSourceContent === undefined) return undefined;
    if (
      includeSourceContent &&
      typeof request?.includeSourceContent !== "boolean"
    ) {
      const consent = await vscode.window.showWarningMessage(
        "This trace archive may include sanitized source-code content. Treat it as sensitive.",
        { modal: true },
        "Include Source Content"
      );
      if (consent !== "Include Source Content") return undefined;
    }
    const trace = await (await this.client()).trace(run.run_id);
    const destination = await this.traceExportDestination(
      trace.trace_id,
      request?.destination
    );
    if (!destination) return undefined;
    const exported = await (await this.client()).exportTrace(
      trace.trace_id,
      includeSourceContent
    );
    if (
      exported.trace_id !== trace.trace_id ||
      exported.run_id !== run.run_id
    ) {
      throw new Error("AgentBus trace export identity did not match the request.");
    }
    if (exported.source_content_included && !includeSourceContent) {
      throw new Error(
        "AgentBus refused an unexpected source-content archive response."
      );
    }
    const bytes = decodeTraceArchive(
      exported.archive_base64,
      exported.archive_sha256
    );
    await vscode.workspace.fs.writeFile(destination, bytes);
    void vscode.window.showInformationMessage(
      `Exported trace ${trace.trace_id} to ${archiveFileLabel(destination)}.`
    );
    return exported;
  }

  private async importTrace(
    raw?: unknown
  ): Promise<TraceArchiveImportResponse | undefined> {
    const request = commandRecord(raw);
    const source = await this.traceImportSource(request?.source);
    if (!source) return undefined;
    const bytes = await vscode.workspace.fs.readFile(source);
    const archiveBase64 = encodeTraceArchive(bytes);
    const explicitConsent =
      typeof request?.allowSourceContent === "boolean";
    let allowSourceContent = request?.allowSourceContent === true;
    let imported: TraceArchiveImportResponse;
    try {
      imported = await (await this.client()).importTrace({
        archive_base64: archiveBase64,
        allow_source_content: allowSourceContent
      });
    } catch (error) {
      if (
        !(error instanceof AgentBusApiError && error.status === 403) ||
        allowSourceContent ||
        explicitConsent
      ) {
        throw error;
      }
      const consent = await vscode.window.showWarningMessage(
        "This archive contains sanitized source content. Import validates and stores it but does not execute or replay it.",
        { modal: true },
        "Import Source Content"
      );
      if (consent !== "Import Source Content") return undefined;
      allowSourceContent = true;
      imported = await (await this.client()).importTrace({
        archive_base64: archiveBase64,
        allow_source_content: true
      });
    }
    if (
      imported.archive_sha256 !== archiveSha256(bytes) ||
      imported.replay_started !== false
    ) {
      throw new Error(
        "AgentBus trace import integrity or no-execution confirmation failed."
      );
    }
    await this.refresh();
    void vscode.window.showInformationMessage(
      `Imported trace ${imported.trace_id}. Replay was not started.`
    );
    return imported;
  }

  private async captureRegressionFixture(
    raw?: unknown
  ): Promise<RegressionFixtureCaptureResponse | undefined> {
    const request = commandRecord(raw);
    const run = await this.resolveRun(request?.runId ?? raw);
    if (!run) return undefined;
    const trace = await (await this.client()).trace(run.run_id);
    const explicitConsent =
      typeof request?.includeSourceContent === "boolean";
    let includeSourceContent = request?.includeSourceContent === true;
    let captured: RegressionFixtureCaptureResponse;
    try {
      captured = await (await this.client()).captureRegressionFixture(
        run.run_id,
        { include_source_content: includeSourceContent }
      );
    } catch (error) {
      if (
        !(error instanceof AgentBusApiError && error.status === 403) ||
        includeSourceContent ||
        explicitConsent
      ) {
        throw error;
      }
      const consent = await vscode.window.showWarningMessage(
        "This portable fixture requires sanitized source-like content. Review its origin and license before sharing it. Capture does not start replay.",
        { modal: true },
        "Capture Source Content"
      );
      if (consent !== "Capture Source Content") return undefined;
      includeSourceContent = true;
      captured = await (await this.client()).captureRegressionFixture(
        run.run_id,
        { include_source_content: true }
      );
    }
    const bytes = decodeRegressionFixtureArchive(
      captured,
      run.run_id,
      trace.trace_id,
      includeSourceContent
    );
    const destination = await this.fixtureDestination(
      trace.trace_id,
      request?.destination
    );
    if (!destination) return undefined;
    await vscode.workspace.fs.writeFile(destination, bytes);
    const sourceNotice = captured.source_content_included
      ? " Sanitized source content is included; review origin and license before sharing."
      : "";
    void vscode.window.showInformationMessage(
      `Captured regression fixture to ${archiveFileLabel(destination)}. Replay was not started.${sourceNotice}`
    );
    return captured;
  }

  private async openProvenanceManifest(raw?: unknown): Promise<void> {
    const run = await this.resolveRun(raw);
    if (!run) return;
    const document = await vscode.workspace.openTextDocument(
      provenanceUri(run.run_id)
    );
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async traceExportDestination(
    traceId: string,
    raw: unknown
  ): Promise<vscode.Uri | undefined> {
    if (typeof raw === "string") {
      validateTraceArchiveFileName(raw);
      return vscode.Uri.file(raw);
    }
    const folder = vscode.workspace.workspaceFolders?.[0]?.uri;
    const destination = await vscode.window.showSaveDialog({
      title: "Export AgentBus Trace",
      defaultUri: folder
        ? vscode.Uri.joinPath(
            folder,
            `${traceId}${TRACE_ARCHIVE_EXTENSION}`
          )
        : undefined,
      filters: {
        "AgentBus Trace Archive": ["agentbus-trace"]
      }
    });
    if (destination) validateTraceArchiveFileName(destination.fsPath);
    return destination;
  }

  private async traceImportSource(raw: unknown): Promise<vscode.Uri | undefined> {
    if (typeof raw === "string") {
      validateTraceArchiveFileName(raw);
      return vscode.Uri.file(raw);
    }
    const selected = await vscode.window.showOpenDialog({
      title: "Import AgentBus Trace",
      canSelectFiles: true,
      canSelectFolders: false,
      canSelectMany: false,
      filters: {
        "AgentBus Trace Archive": ["agentbus-trace"]
      }
    });
    const source = selected?.[0];
    if (source) validateTraceArchiveFileName(source.fsPath);
    return source;
  }

  private async fixtureDestination(
    traceId: string,
    raw: unknown
  ): Promise<vscode.Uri | undefined> {
    if (typeof raw === "string") {
      validateTraceArchiveFileName(raw);
      return vscode.Uri.file(raw);
    }
    const folder = vscode.workspace.workspaceFolders?.[0]?.uri;
    const destination = await vscode.window.showSaveDialog({
      title: "Capture AgentBus Regression Fixture",
      defaultUri: folder
        ? vscode.Uri.joinPath(
            folder,
            `${traceId}.regression${TRACE_ARCHIVE_EXTENSION}`
          )
        : undefined,
      filters: {
        "AgentBus Regression Fixture": ["agentbus-trace"]
      }
    });
    if (destination) validateTraceArchiveFileName(destination.fsPath);
    return destination;
  }

  private async prepareOfflineReplay(runId: string): Promise<boolean> {
    const client = await this.client();
    const replayability = await client.replayability(runId, 0, 500);
    const document = await vscode.workspace.openTextDocument(
      replayPlanUri(runId, "offline")
    );
    await vscode.window.showTextDocument(document, { preview: true });
    const blocked = offlineReplayBlockReason(replayability);
    if (!blocked) return true;
    void vscode.window.showWarningMessage(blocked);
    return false;
  }

  private async submitReplay(
    runId: string,
    request: ReplayCreateRequest
  ): Promise<ReplayAcceptedResponse> {
    const accepted = await (await this.client()).createReplay(runId, request);
    this.refreshViews();
    await vscode.commands.executeCommand("agentbus.replays.focus");
    void vscode.window.showInformationMessage(
      `Offline replay ${accepted.replay_id} started. The source repository will not be mutated.`
    );
    this.startReplayMonitor(accepted.replay_id);
    return accepted;
  }

  private async cancelReplay(
    raw?: unknown
  ): Promise<ReplayCancelResponse | undefined> {
    const item = raw as AgentBusItem | undefined;
    let replay = item?.value as ReplaySessionResponse | undefined;
    if (typeof raw === "string") {
      replay = await (await this.client()).replay(raw);
    }
    if (!replay) {
      const response = await (await this.client()).listReplays(
        undefined,
        undefined,
        500
      );
      replay = (
        await vscode.window.showQuickPick(
          response.replays
            .filter((value) => !isTerminalReplayStatus(value.status))
            .map((value) => ({
              label: value.replay_id,
              description: `${value.mode} | ${value.status}`,
              value
            })),
          { title: "Cancel AgentBus Replay" }
        )
      )?.value;
    }
    if (!replay || isTerminalReplayStatus(replay.status)) return undefined;
    const cancelled = await (await this.client()).cancelReplay(
      replay.replay_id
    );
    this.refreshViews();
    return cancelled;
  }

  private startReplayMonitor(replayId: string): void {
    if (this.replayMonitors.has(replayId)) return;
    const controller = new AbortController();
    this.replayMonitors.set(replayId, controller);
    void this.monitorReplay(replayId, controller.signal)
      .catch((error: unknown) => {
        this.output.appendLine(
          `Replay ${replayId} monitor: ${safeError(error)}`
        );
      })
      .finally(() => {
        if (this.replayMonitors.get(replayId) === controller) {
          this.replayMonitors.delete(replayId);
        }
      });
  }

  private async monitorReplay(
    replayId: string,
    signal: AbortSignal
  ): Promise<void> {
    const deadline = Date.now() + 300_000;
    let delayMilliseconds = 100;
    while (!signal.aborted && Date.now() < deadline) {
      await abortableDelay(delayMilliseconds, signal);
      if (signal.aborted) return;
      const replay = await (await this.client()).replay(replayId);
      this.refreshViews();
      if (isTerminalReplayStatus(replay.status)) {
        if (replay.comparison_id) {
          const comparison = await (await this.client()).comparison(
            replay.comparison_id,
            0,
            500
          );
          await this.comparisons.upsert(comparison);
          this.refreshViews();
        }
        const message = `AgentBus replay ${replayId} ${replay.status}.`;
        if (replay.status === "succeeded") {
          void vscode.window.showInformationMessage(message);
        } else {
          void vscode.window.showWarningMessage(message);
        }
        return;
      }
      delayMilliseconds = Math.min(delayMilliseconds * 2, 1_000);
    }
    if (!signal.aborted) {
      this.output.appendLine(
        `Replay ${replayId} monitor reached its bounded deadline.`
      );
    }
  }

  private async compareRuns(
    leftRaw?: unknown,
    rightRaw?: unknown
  ): Promise<ComparisonResponse | undefined> {
    const left = await this.chooseComparisonTarget(
      "Select Left AgentBus Run",
      leftRaw
    );
    if (!left) return undefined;
    const right = await this.chooseComparisonTarget(
      "Select Right AgentBus Run",
      rightRaw,
      left
    );
    if (!right) return undefined;
    const comparison = await (await this.client()).createComparison(
      { left, right },
      0,
      500
    );
    await this.comparisons.upsert(comparison);
    this.refreshViews();
    await this.openComparison(comparison.comparison_id);
    return comparison;
  }

  private async showComparison(raw?: unknown): Promise<void> {
    const comparison = await this.resolveComparison(raw);
    if (!comparison) return;
    await this.openComparison(comparison.comparison_id);
  }

  private async openStructuredReplayDifferences(
    raw?: unknown
  ): Promise<void> {
    const comparison = await this.resolveComparison(raw);
    if (!comparison) return;
    await vscode.commands.executeCommand(
      "vscode.diff",
      comparisonSideUri(comparison.comparison_id, "left"),
      comparisonSideUri(comparison.comparison_id, "right"),
      `AgentBus ${comparison.comparison_id} (hashes only)`
    );
  }

  private async compareRunReports(
    leftRaw?: unknown,
    rightRaw?: unknown
  ): Promise<void> {
    const left = await this.chooseComparisonTarget(
      "Select Left AgentBus Report",
      leftRaw
    );
    if (!left) return;
    const right = await this.chooseComparisonTarget(
      "Select Right AgentBus Report",
      rightRaw,
      left
    );
    if (!right) return;
    await vscode.commands.executeCommand(
      "vscode.diff",
      reportUri(left),
      reportUri(right),
      "AgentBus Run Reports"
    );
  }

  private async resolveComparison(
    raw?: unknown
  ): Promise<ComparisonResponse | undefined> {
    const item = raw as AgentBusItem | undefined;
    const treeValue = item?.value as
      | { comparison?: ComparisonResponse }
      | undefined;
    let comparison = treeValue?.comparison;
    if (!comparison) {
      const loaded = await this.comparisons.load(await this.client());
      comparison = (
        await vscode.window.showQuickPick(
          loaded.map((value) => ({
            label: value.comparison_id,
            description: `${value.summary.changed_spans} changed span(s)`,
            value
          })),
          { title: "Show AgentBus Comparison" }
        )
      )?.value;
    }
    return comparison;
  }

  private async openComparison(comparisonId: string): Promise<void> {
    const document = await vscode.workspace.openTextDocument(
      comparisonUri(comparisonId)
    );
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async chooseComparisonTarget(
    title: string,
    raw?: unknown,
    excluded?: string
  ): Promise<string | undefined> {
    if (typeof raw === "string") {
      if (!isSafeControlId(raw) || raw === excluded) {
        throw new Error("AgentBus comparison identifiers must be safe and distinct.");
      }
      return raw;
    }
    return (
      await vscode.window.showQuickPick(
        this.store
          .runs()
          .filter((run) => run.run_id !== excluded)
          .map((run) => ({
            label: run.original_task,
            description: `${run.status} | ${run.run_id}`,
            value: run.run_id
          })),
        { title }
      )
    )?.value;
  }

  private async resolveRun(raw?: unknown): Promise<RunSummary | undefined> {
    if (typeof raw === "string") {
      const cached = this.store.run(raw);
      return cached ?? (await (await this.client()).run(raw));
    }
    const item = raw as AgentBusItem | undefined;
    const value = item?.value as RunSummary | undefined;
    return value?.run_id ? value : this.chooseRun();
  }

  private async resume(): Promise<void> {
    if (!(await ensureWorkspaceTrust("run resume"))) return;
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
    if (decision === "approve" && !(await ensureWorkspaceTrust("approval"))) {
      return;
    }
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

function abortableDelay(
  milliseconds: number,
  signal: AbortSignal
): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const onAbort = (): void => {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      resolve();
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function commandRecord(
  value: unknown
): Record<string, unknown> | undefined {
  return isRecord(value) ? value : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function archiveFileLabel(uri: vscode.Uri): string {
  return decodeURIComponent(uri.path.split("/").pop() ?? "trace archive");
}
