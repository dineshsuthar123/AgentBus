import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import type {
  WorkspaceContextPlanResponse,
  WorkspaceImpactResponse,
  WorkspaceIndexMutationResponse,
  WorkspaceIndexStatusResponse,
  WorkspaceSearchResponse,
  WorkspaceTestsResponse
} from "./generated/protocol";
import { safeError } from "./redaction";
import {
  canonicalWorkspacePath,
  ensureWorkspaceTrust,
  selectWorkspace
} from "./workspace";

type ClientProvider = () => Promise<AgentBusClient>;

export interface IntelligenceWorkspaceState {
  readonly folder: vscode.WorkspaceFolder;
  readonly canonicalPath: string;
  readonly status?: WorkspaceIndexStatusResponse;
  readonly error?: string;
  readonly search?: WorkspaceSearchResponse;
  readonly impact?: WorkspaceImpactResponse;
  readonly tests?: WorkspaceTestsResponse;
  readonly contextPlan?: WorkspaceContextPlanResponse;
}

export class RepositoryIntelligenceState implements vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<void>();
  private readonly records = new Map<string, IntelligenceWorkspaceState>();
  private readonly attaching = new Map<
    string,
    Promise<IntelligenceWorkspaceState>
  >();
  public readonly onDidChange = this.changed.event;

  public constructor(private readonly clientProvider: ClientProvider) {}

  public client(): Promise<AgentBusClient> {
    return this.clientProvider();
  }

  public async workspaces(): Promise<IntelligenceWorkspaceState[]> {
    const values: IntelligenceWorkspaceState[] = [];
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      values.push(await this.attachFolder(folder));
    }
    return values;
  }

  public async find(
    workspaceId: string
  ): Promise<IntelligenceWorkspaceState | undefined> {
    return (await this.workspaces()).find(
      (workspace) => workspace.status?.workspace_id === workspaceId
    );
  }

  public async select(
    requireTrust: boolean,
    operation: string
  ): Promise<IntelligenceWorkspaceState | undefined> {
    if (requireTrust && !(await ensureWorkspaceTrust(operation))) {
      return undefined;
    }
    const folder = await selectWorkspace(false);
    return folder ? this.attachFolder(folder, true) : undefined;
  }

  public async attachFolder(
    folder: vscode.WorkspaceFolder,
    force = false
  ): Promise<IntelligenceWorkspaceState> {
    let canonicalPath: string;
    try {
      canonicalPath = await canonicalWorkspacePath(folder);
    } catch (error) {
      return this.failedRecord(folder, folder.uri.fsPath, error);
    }
    const key = folder.uri.toString();
    const cached = this.records.get(key);
    if (!force && cached?.status) return cached;
    const pending = this.attaching.get(key);
    if (pending) return pending;
    const operation = this.attach(folder, canonicalPath, key, cached);
    this.attaching.set(key, operation);
    try {
      return await operation;
    } finally {
      this.attaching.delete(key);
    }
  }

  public async recordMutation(
    workspace: IntelligenceWorkspaceState,
    mutation: WorkspaceIndexMutationResponse
  ): Promise<IntelligenceWorkspaceState> {
    const client = await this.clientProvider();
    let status: WorkspaceIndexStatusResponse = {
      workspace_id: mutation.workspace_id,
      repository_id: mutation.repository_id,
      status: mutation.result.status
    };
    try {
      status = await client.workspaceIndexStatus(mutation.workspace_id);
    } catch {
      // The mutation response still provides a safe, current lifecycle state.
    }
    return this.update(workspace, { status, error: undefined });
  }

  public recordSearch(
    workspace: IntelligenceWorkspaceState,
    search: WorkspaceSearchResponse
  ): void {
    this.update(workspace, { search });
  }

  public recordImpact(
    workspace: IntelligenceWorkspaceState,
    impact: WorkspaceImpactResponse
  ): void {
    this.update(workspace, { impact });
  }

  public recordTests(
    workspace: IntelligenceWorkspaceState,
    tests: WorkspaceTestsResponse
  ): void {
    this.update(workspace, { tests });
  }

  public recordContextPlan(
    workspace: IntelligenceWorkspaceState,
    contextPlan: WorkspaceContextPlanResponse
  ): void {
    this.update(workspace, { contextPlan });
  }

  public refresh(): void {
    for (const [key, record] of this.records) {
      this.records.set(key, {
        ...record,
        status: undefined,
        error: undefined
      });
    }
    this.changed.fire();
  }

  public dispose(): void {
    this.attaching.clear();
    this.records.clear();
    this.changed.dispose();
  }

  private async attach(
    folder: vscode.WorkspaceFolder,
    canonicalPath: string,
    key: string,
    cached: IntelligenceWorkspaceState | undefined
  ): Promise<IntelligenceWorkspaceState> {
    try {
      const status = await (
        await this.clientProvider()
      ).attachWorkspaceIndex({ workspace: canonicalPath });
      const record: IntelligenceWorkspaceState = {
        ...cached,
        folder,
        canonicalPath,
        status,
        error: undefined
      };
      this.records.set(key, record);
      return record;
    } catch (error) {
      return this.failedRecord(folder, canonicalPath, error, cached);
    }
  }

  private failedRecord(
    folder: vscode.WorkspaceFolder,
    canonicalPath: string,
    error: unknown,
    cached?: IntelligenceWorkspaceState
  ): IntelligenceWorkspaceState {
    const record: IntelligenceWorkspaceState = {
      ...cached,
      folder,
      canonicalPath,
      status: undefined,
      error: safeError(error)
    };
    this.records.set(folder.uri.toString(), record);
    return record;
  }

  private update(
    workspace: IntelligenceWorkspaceState,
    values: Partial<IntelligenceWorkspaceState>
  ): IntelligenceWorkspaceState {
    const record = { ...workspace, ...values };
    this.records.set(workspace.folder.uri.toString(), record);
    this.changed.fire();
    return record;
  }
}
