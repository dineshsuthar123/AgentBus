import * as vscode from "vscode";
import type {
  ArchitectureBoundarySummary,
  ContextRole,
  OwnershipRuleSummary,
  SymbolSummary,
  WorkspaceIndexMutationResponse
} from "./generated/protocol";
import {
  formatArchitectureDocument,
  formatContextPlanDocument,
  formatGraphDocument,
  formatImpactDocument,
  formatOwnershipDocument,
  formatSearchDocument,
  formatSymbolDocument,
  formatTestsDocument,
  indexWarning,
  parseImpactSubjects,
  validateRepositoryQuery,
  type IntelligenceTarget
} from "./intelligencePresentation";
import { showIntelligenceDocument } from "./intelligenceDocuments";
import type { IntelligenceDocumentProvider } from "./intelligenceDocuments";
import type {
  IntelligenceWorkspaceState,
  RepositoryIntelligenceState
} from "./intelligenceState";
import {
  targetFromTreeItem,
  workspaceFromTreeItem
} from "./intelligenceViews";
import { safeError } from "./redaction";
import { ensureWorkspaceTrust } from "./workspace";

type IndexMutation = "build" | "update" | "repair";

interface SymbolChoice extends vscode.QuickPickItem {
  symbol: SymbolSummary;
}

type ArchitectureChoice =
  | (vscode.QuickPickItem & {
      choiceType: "boundary";
      value: ArchitectureBoundarySummary;
    })
  | (vscode.QuickPickItem & {
      choiceType: "ownership";
      value: OwnershipRuleSummary;
    });

export class IntelligenceCommandController implements vscode.Disposable {
  private readonly disposables: vscode.Disposable[] = [];
  private readonly mutationsInFlight = new Set<string>();

  public constructor(
    private readonly state: RepositoryIntelligenceState,
    private readonly documents: IntelligenceDocumentProvider,
    private readonly output: vscode.OutputChannel
  ) {}

  public register(context: vscode.ExtensionContext): void {
    const command = (name: string, action: (...args: unknown[]) => Promise<void>) =>
      this.disposables.push(
        vscode.commands.registerCommand(name, (...args) =>
          this.execute(() => action(...args))
        )
      );
    command("agentbus.buildRepositoryIndex", (item) =>
      this.mutateIndex("build", item)
    );
    command("agentbus.updateRepositoryIndex", (item) =>
      this.mutateIndex("update", item)
    );
    command("agentbus.verifyRepositoryIndex", (item) =>
      this.verifyIndex(item)
    );
    command("agentbus.repairRepositoryIndex", (item) =>
      this.mutateIndex("repair", item)
    );
    command("agentbus.cancelRepositoryIndex", (item) =>
      this.cancelIndex(item)
    );
    command("agentbus.searchRepository", (query) =>
      this.searchRepository(query)
    );
    command("agentbus.findRepositorySymbol", (query) =>
      this.findSymbol(query)
    );
    command("agentbus.openRepositorySymbol", (target) =>
      this.openSymbol(target)
    );
    command("agentbus.showRepositoryDependencies", (target) =>
      this.showGraph("dependencies", target)
    );
    command("agentbus.showRepositoryDependents", (target) =>
      this.showGraph("dependents", target)
    );
    command("agentbus.analyzeChangeImpact", (target) =>
      this.analyzeImpact(target)
    );
    command("agentbus.findRelevantTests", (target) =>
      this.findTests(target)
    );
    command("agentbus.previewAgentContext", (target) =>
      this.previewContext(target)
    );
    command("agentbus.openArchitectureBoundary", (target) =>
      this.openArchitecture(target)
    );
    context.subscriptions.push(...this.disposables);
  }

  public dispose(): void {
    for (const disposable of this.disposables) disposable.dispose();
    this.disposables.length = 0;
    this.mutationsInFlight.clear();
  }

  private async mutateIndex(
    operation: IndexMutation,
    argument: unknown
  ): Promise<void> {
    const workspace = await this.workspaceFor(
      argument,
      true,
      `${operation} repository index`
    );
    if (!workspace?.status) return;
    if (this.mutationsInFlight.has(workspace.canonicalPath)) {
      void vscode.window.showWarningMessage(
        "A repository index operation is already active for this workspace."
      );
      return;
    }
    this.mutationsInFlight.add(workspace.canonicalPath);
    try {
      const result = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `AgentBus: ${titleCase(operation)} Repository Index`,
          cancellable: true
        },
        async (progress, token) =>
          this.performMutation(workspace, operation, progress, token)
      );
      if (!result) return;
      await this.state.recordMutation(workspace, result);
      const counts = result.result;
      void vscode.window.showInformationMessage(
        `Repository index ${operation} completed: ${counts.indexed_count} indexed, ${counts.reused_count} reused, ${counts.skipped_count} skipped.`
      );
      this.output.appendLine(
        `[repository-index] ${operation} completed (${counts.snapshot.state}).`
      );
    } finally {
      this.mutationsInFlight.delete(workspace.canonicalPath);
      this.state.refresh();
    }
  }

  private async performMutation(
    workspace: IntelligenceWorkspaceState,
    operation: IndexMutation,
    progress: vscode.Progress<{ message?: string; increment?: number }>,
    token: vscode.CancellationToken
  ): Promise<WorkspaceIndexMutationResponse | undefined> {
    const client = await this.state.client();
    const workspaceId = workspace.status?.workspace_id;
    if (!workspaceId) throw new Error("Repository workspace is not attached.");
    const abort = new AbortController();
    let cancellationRequested = false;
    const cancellation = token.onCancellationRequested(() => {
      cancellationRequested = true;
      progress.report({ message: "Requesting safe cancellation..." });
      void client
        .cancelWorkspaceIndex(workspaceId)
        .then((response) => {
          if (response.cancellation_requested) {
            abort.abort(new Error("Repository index cancellation requested."));
          }
        })
        .catch((error: unknown) => {
          this.output.appendLine(
            `[repository-index] cancellation: ${safeError(error)}`
          );
        });
    });
    const timer = setInterval(() => {
      void client
        .workspaceIndexStatus(workspaceId)
        .then((response) => {
          progress.report({
            message:
              response.status.message ??
              `Index state: ${response.status.state}`
          });
        })
        .catch(() => undefined);
    }, 750);
    progress.report({ message: "Validating repository and index lease..." });
    try {
      if (operation === "build") {
        return await client.buildWorkspaceIndex(
          {
            workspace: workspace.canonicalPath,
            workspace_trusted: true
          },
          abort.signal
        );
      }
      const action = { workspace_trusted: true };
      return operation === "update"
        ? await client.updateWorkspaceIndex(workspaceId, action, abort.signal)
        : await client.repairWorkspaceIndex(workspaceId, action, abort.signal);
    } catch (error) {
      if (cancellationRequested || abort.signal.aborted) {
        void vscode.window.showInformationMessage(
          "Repository index cancellation was requested. Files were not deleted or rolled back."
        );
        return undefined;
      }
      throw error;
    } finally {
      clearInterval(timer);
      cancellation.dispose();
    }
  }

  private async verifyIndex(argument: unknown): Promise<void> {
    const workspace = await this.workspaceFor(
      argument,
      false,
      "verify repository index"
    );
    if (!workspace?.status) return;
    const result = await (
      await this.state.client()
    ).verifyWorkspaceIndex(workspace.status.workspace_id);
    this.state.refresh();
    void vscode.window.showInformationMessage(
      result.result.valid && result.result.fresh
        ? "Repository index is valid and current."
        : `Repository index verification: valid=${result.result.valid}, fresh=${result.result.fresh}, state=${result.result.status.state}.`
    );
  }

  private async cancelIndex(argument: unknown): Promise<void> {
    const workspace = await this.workspaceFor(
      argument,
      false,
      "cancel repository indexing"
    );
    if (!workspace?.status) return;
    const response = await (
      await this.state.client()
    ).cancelWorkspaceIndex(workspace.status.workspace_id);
    void vscode.window.showInformationMessage(
      response.cancellation_requested
        ? "Repository index cancellation requested."
        : "No active repository index operation accepted cancellation."
    );
  }

  private async searchRepository(argument: unknown): Promise<void> {
    const workspace = await this.workspaceFor(
      undefined,
      false,
      "search repository"
    );
    if (!workspace || !(await this.queryable(workspace))) return;
    const response = await this.search(workspace, argument, "Search Repository");
    if (!response) return;
    this.state.recordSearch(workspace, response);
    const choices = symbolChoices(response.report.results ?? []);
    if (choices.length === 0) {
      await showIntelligenceDocument(
        this.documents,
        "search",
        "repository-search",
        formatSearchDocument(response)
      );
      return;
    }
    const picked = await vscode.window.showQuickPick(choices, {
      title: "Repository Search Results",
      placeHolder: "Select a symbol to inspect; results also appear in Symbol Explorer"
    });
    if (picked) {
      await this.openSymbol({
        kind: "symbol",
        workspaceId: workspace.status?.workspace_id ?? "",
        symbolId: picked.symbol.symbol_id
      });
    }
  }

  private async findSymbol(argument: unknown): Promise<void> {
    const resolved = await this.resolveSymbol(argument, "Find Symbol");
    if (resolved) await this.openSymbolTarget(resolved.workspace, resolved.target);
  }

  private async openSymbol(argument: unknown): Promise<void> {
    const resolved = await this.resolveSymbol(argument, "Find Symbol");
    if (resolved) await this.openSymbolTarget(resolved.workspace, resolved.target);
  }

  private async showGraph(
    direction: "dependencies" | "dependents",
    argument: unknown
  ): Promise<void> {
    const resolved = await this.resolveSymbol(
      argument,
      direction === "dependencies" ? "Show Dependencies" : "Show Dependents"
    );
    if (!resolved) return;
    const response = await (
      await this.state.client()
    ).repositoryGraph(
      resolved.target.workspaceId,
      resolved.target.symbolId,
      direction,
      {
        depth: 4,
        limit: 500,
        includeUnresolved: true,
        includeEvidence: true
      }
    );
    await showIntelligenceDocument(
      this.documents,
      direction,
      `${direction}-${response.subject.name}`,
      formatGraphDocument(response)
    );
  }

  private async analyzeImpact(argument: unknown): Promise<void> {
    const request = await this.impactRequest(argument, "Analyze Change Impact");
    if (!request) return;
    const response = await (
      await this.state.client()
    ).analyzeRepositoryImpact(request.workspace.status!.workspace_id, {
      subjects: request.subjects,
      max_depth: 4,
      max_nodes: 500,
      include_evidence: true
    });
    this.state.recordImpact(request.workspace, response);
    await showIntelligenceDocument(
      this.documents,
      "impact",
      "change-impact",
      formatImpactDocument(response)
    );
  }

  private async findTests(argument: unknown): Promise<void> {
    const request = await this.impactRequest(argument, "Find Relevant Tests");
    if (!request) return;
    const response = await (
      await this.state.client()
    ).repositoryTests(request.workspace.status!.workspace_id, {
      subjects: request.subjects,
      max_depth: 4,
      max_nodes: 500,
      include_evidence: true
    });
    this.state.recordTests(request.workspace, response);
    await showIntelligenceDocument(
      this.documents,
      "tests",
      "relevant-tests",
      formatTestsDocument(response)
    );
  }

  private async previewContext(argument: unknown): Promise<void> {
    const workspace = await this.workspaceFor(
      argument,
      false,
      "preview agent context"
    );
    if (!workspace || !(await this.queryable(workspace))) return;
    const task = await vscode.window.showInputBox({
      title: "Preview Agent Context",
      prompt: "Describe the task. The text is sent only to the authenticated local daemon.",
      placeHolder: "Change the calculator endpoint and update its tests",
      validateInput: (value) =>
        validateTask(value) ? undefined : "Task must be 1 to 20000 characters."
    });
    if (task === undefined) return;
    const role = await vscode.window.showQuickPick<
      vscode.QuickPickItem & { role: ContextRole }
    >(
      ["planner", "coder", "verifier", "reviewer"].map((value) => ({
        label: titleCase(value),
        role: value as ContextRole
      })),
      { title: "Select Agent Role" }
    );
    if (!role) return;
    const changedPath = activeRelativePath(workspace);
    const response = await (
      await this.state.client()
    ).repositoryContextPlan(workspace.status!.workspace_id, {
      task: task.trim(),
      role: role.role,
      changed_paths: changedPath ? [changedPath] : [],
      byte_budget: 100_000,
      token_budget: 16_000,
      include_evidence: true
    });
    this.state.recordContextPlan(workspace, response);
    await showIntelligenceDocument(
      this.documents,
      "context",
      `${role.role}-context-plan`,
      formatContextPlanDocument(response)
    );
  }

  private async openArchitecture(argument: unknown): Promise<void> {
    const target = targetFromTreeItem(argument);
    const workspace = target
      ? await this.state.find(target.workspaceId)
      : await this.workspaceFor(undefined, false, "open architecture boundary");
    if (!workspace?.status || !(await this.queryable(workspace))) return;
    const overview = workspace.status.overview;
    if (!overview) throw new Error("Repository overview is unavailable.");
    let choice: ArchitectureChoice | undefined;
    if (target?.kind === "boundary") {
      const value = (overview.architecture_boundaries ?? []).find(
        (item) => item.boundary_id === target.boundaryId
      );
      if (value) choice = boundaryChoice(value);
    } else if (target?.kind === "ownership") {
      const value = (overview.ownership_rules ?? []).find(
        (item) => item.rule_id === target.ruleId
      );
      if (value) choice = ownershipChoice(value);
    } else {
      choice = await vscode.window.showQuickPick(
        [
          ...(overview.architecture_boundaries ?? []).map(boundaryChoice),
          ...(overview.ownership_rules ?? []).map(ownershipChoice)
        ],
        {
          title: "Open Architecture Boundary",
          placeHolder: "Select an evidence-backed boundary or ownership rule"
        }
      );
    }
    if (!choice) {
      void vscode.window.showInformationMessage(
        "No matching architecture boundary or ownership rule is available."
      );
      return;
    }
    if (choice.choiceType === "boundary") {
      await showIntelligenceDocument(
        this.documents,
        "architecture",
        choice.value.name,
        formatArchitectureDocument(choice.value)
      );
    } else {
      await showIntelligenceDocument(
        this.documents,
        "ownership",
        "ownership-rule",
        formatOwnershipDocument(choice.value)
      );
    }
  }

  private async impactRequest(
    argument: unknown,
    title: string
  ): Promise<{ workspace: IntelligenceWorkspaceState; subjects: string[] } | undefined> {
    const target = targetFromTreeItem(argument);
    const workspace = target
      ? await this.state.find(target.workspaceId)
      : await this.workspaceFor(argument, false, title.toLowerCase());
    if (!workspace || !(await this.queryable(workspace))) return undefined;
    const defaultValue =
      target?.kind === "symbol"
        ? target.symbolId
        : activeRelativePath(workspace) ?? "";
    const input = await vscode.window.showInputBox({
      title,
      prompt: "Enter repository-relative paths or symbol identities, separated by commas.",
      value: defaultValue,
      validateInput: (value) => {
        try {
          parseImpactSubjects(value);
          return undefined;
        } catch (error) {
          return safeError(error);
        }
      }
    });
    return input === undefined
      ? undefined
      : { workspace, subjects: parseImpactSubjects(input) };
  }

  private async resolveSymbol(
    argument: unknown,
    title: string
  ): Promise<
    | {
        workspace: IntelligenceWorkspaceState;
        target: Extract<IntelligenceTarget, { kind: "symbol" }>;
      }
    | undefined
  > {
    const target = targetFromTreeItem(argument);
    if (target?.kind === "symbol") {
      const workspace = await this.state.find(target.workspaceId);
      if (!workspace || !(await this.queryable(workspace))) return undefined;
      return { workspace, target };
    }
    const workspace = await this.workspaceFor(undefined, false, title.toLowerCase());
    if (!workspace || !(await this.queryable(workspace))) return undefined;
    const response = await this.search(workspace, argument, title);
    if (!response) return undefined;
    this.state.recordSearch(workspace, response);
    const choice = await vscode.window.showQuickPick(
      symbolChoices(response.report.results ?? []),
      {
        title,
        placeHolder: "Select a repository symbol"
      }
    );
    return choice
      ? {
          workspace,
          target: {
            kind: "symbol",
            workspaceId: workspace.status!.workspace_id,
            symbolId: choice.symbol.symbol_id
          }
        }
      : undefined;
  }

  private async search(
    workspace: IntelligenceWorkspaceState,
    argument: unknown,
    title: string
  ) {
    const supplied = typeof argument === "string" ? argument : undefined;
    const input =
      supplied ??
      (await vscode.window.showInputBox({
        title,
        prompt: "Search indexed paths, symbols, endpoints, tests, and configuration.",
        validateInput: (value) => {
          try {
            validateRepositoryQuery(value);
            return undefined;
          } catch (error) {
            return safeError(error);
          }
        }
      }));
    if (input === undefined) return undefined;
    return (await this.state.client()).searchRepository(
      workspace.status!.workspace_id,
      {
        query: validateRepositoryQuery(input),
        limit: 100,
        include_evidence: true
      }
    );
  }

  private async openSymbolTarget(
    workspace: IntelligenceWorkspaceState,
    target: Extract<IntelligenceTarget, { kind: "symbol" }>
  ): Promise<void> {
    if (!(await this.queryable(workspace))) return;
    const response = await (
      await this.state.client()
    ).repositorySymbol(target.workspaceId, target.symbolId, true);
    await showIntelligenceDocument(
      this.documents,
      "symbol",
      response.symbol.name,
      formatSymbolDocument(response)
    );
  }

  private async workspaceFor(
    argument: unknown,
    requireTrust: boolean,
    operation: string
  ): Promise<IntelligenceWorkspaceState | undefined> {
    const existing = workspaceFromTreeItem(argument);
    if (existing) {
      if (requireTrust && !(await ensureWorkspaceTrust(operation))) {
        return undefined;
      }
      return this.state.attachFolder(existing.folder, true);
    }
    return this.state.select(requireTrust, operation);
  }

  private async queryable(workspace: IntelligenceWorkspaceState): Promise<boolean> {
    if (!workspace.status) {
      void vscode.window.showErrorMessage(
        workspace.error ?? "Repository intelligence is unavailable."
      );
      return false;
    }
    const warning = indexWarning(workspace.status);
    if (warning) void vscode.window.showWarningMessage(warning);
    const status = workspace.status.status;
    return ![
      "absent",
      "corrupted",
      "incompatible"
    ].includes(status.state) && Boolean(status.snapshot_id);
  }

  private async execute(action: () => Promise<void>): Promise<void> {
    try {
      await action();
    } catch (error) {
      const message = safeError(error);
      this.output.appendLine(`[repository-intelligence] ${message}`);
      void vscode.window.showErrorMessage(message);
    }
  }
}

function symbolChoices(
  results: ReadonlyArray<{
    symbol?: SymbolSummary | null;
    explanation: string;
  }>
): SymbolChoice[] {
  return results
    .map((result) => result.symbol)
    .filter((symbol): symbol is SymbolSummary => Boolean(symbol))
    .slice(0, 100)
    .map((symbol) => ({
      label: safePickerText(symbol.qualified_name),
      description: safePickerText(
        `${symbol.kind} | ${symbol.relative_path}:${symbol.start_line}`
      ),
      detail: safePickerText(
        `${Math.round(symbol.confidence * 100)}% confidence`
      ),
      symbol
    }));
}

function boundaryChoice(
  value: ArchitectureBoundarySummary
): ArchitectureChoice {
  return {
    choiceType: "boundary",
    value,
    label: safePickerText(value.name),
    description: safePickerText(value.boundary_type),
    detail: safePickerText(
      `${Math.round(value.confidence * 100)}% confidence | ${value.explanation}`
    )
  };
}

function ownershipChoice(value: OwnershipRuleSummary): ArchitectureChoice {
  return {
    choiceType: "ownership",
    value,
    label: safePickerText(value.pattern),
    description: safePickerText(value.owners.join(", ")),
    detail: safePickerText(value.explanation)
  };
}

function activeRelativePath(
  workspace: IntelligenceWorkspaceState
): string | undefined {
  const uri = vscode.window.activeTextEditor?.document.uri;
  if (!uri || uri.scheme !== "file") return undefined;
  const folder = vscode.workspace.getWorkspaceFolder(uri);
  if (!folder || folder.index !== workspace.folder.index) return undefined;
  return vscode.workspace.asRelativePath(uri, false).replace(/\\/g, "/");
}

function validateTask(value: string): boolean {
  const task = value.trim();
  return Boolean(task && task.length <= 20_000 && !task.includes("\0"));
}

function titleCase(value: string): string {
  return value.replace(/\b\w/g, (character) => character.toUpperCase());
}

function safePickerText(value: unknown): string {
  return redactPickerText(value).replace(/[\r\n]+/g, " ");
}

function redactPickerText(value: unknown): string {
  return safeError(String(value)).slice(0, 1_024);
}
