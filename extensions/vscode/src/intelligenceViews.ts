import * as vscode from "vscode";
import {
  contextPlanTree,
  impactTree,
  repositoryTree,
  symbolTree,
  type IntelligenceTarget,
  type IntelligenceTreeEntry
} from "./intelligencePresentation";
import type {
  IntelligenceWorkspaceState,
  RepositoryIntelligenceState
} from "./intelligenceState";
import { SyndraItem } from "./views";

type IntelligenceViewKind = "repository" | "symbols" | "impact" | "context";

interface WorkspaceValue {
  kind: "intelligence-workspace";
  view: IntelligenceViewKind;
  workspace: IntelligenceWorkspaceState;
}

interface EntryValue {
  kind: "intelligence-entry";
  entry: IntelligenceTreeEntry;
}

type IntelligenceTreeValue = WorkspaceValue | EntryValue;

abstract class IntelligenceProvider
  implements vscode.TreeDataProvider<SyndraItem>, vscode.Disposable
{
  private readonly changed = new vscode.EventEmitter<
    SyndraItem | undefined | void
  >();
  private readonly stateSubscription: vscode.Disposable;
  public readonly onDidChangeTreeData = this.changed.event;

  protected constructor(
    private readonly state: RepositoryIntelligenceState,
    private readonly view: IntelligenceViewKind
  ) {
    this.stateSubscription = state.onDidChange(() => this.changed.fire());
  }

  public getTreeItem(element: SyndraItem): vscode.TreeItem {
    return element;
  }

  public async getChildren(element?: SyndraItem): Promise<SyndraItem[]> {
    if (!element) {
      const workspaces = await this.state.workspaces();
      if (workspaces.length === 0) {
        return [messageItem("Open a repository folder to inspect intelligence.")];
      }
      return workspaces.map((workspace) => workspaceItem(workspace, this.view));
    }
    const value = element.value as IntelligenceTreeValue | undefined;
    if (value?.kind === "intelligence-workspace") {
      return this.workspaceChildren(value.workspace).map(entryItem);
    }
    if (value?.kind === "intelligence-entry") {
      return (value.entry.children ?? []).map(entryItem);
    }
    return [];
  }

  public dispose(): void {
    this.stateSubscription.dispose();
    this.changed.dispose();
  }

  private workspaceChildren(
    workspace: IntelligenceWorkspaceState
  ): IntelligenceTreeEntry[] {
    if (!workspace.status) {
      return [
        {
          id: "unavailable",
          label: "Repository intelligence unavailable",
          description: workspace.error,
          tooltip: workspace.error,
          icon: "error",
          contextValue: "agentbusIntelligenceItem"
        }
      ];
    }
    switch (this.view) {
      case "repository":
        return repositoryTree(workspace.status);
      case "symbols":
        return symbolTree(workspace.status, workspace.search);
      case "impact":
        return impactTree(workspace.impact, workspace.tests);
      case "context":
        return contextPlanTree(workspace.contextPlan);
    }
  }
}

export class RepositoryIntelligenceProvider extends IntelligenceProvider {
  public constructor(state: RepositoryIntelligenceState) {
    super(state, "repository");
  }
}

export class SymbolExplorerProvider extends IntelligenceProvider {
  public constructor(state: RepositoryIntelligenceState) {
    super(state, "symbols");
  }
}

export class ImpactAnalysisProvider extends IntelligenceProvider {
  public constructor(state: RepositoryIntelligenceState) {
    super(state, "impact");
  }
}

export class ContextPlanProvider extends IntelligenceProvider {
  public constructor(state: RepositoryIntelligenceState) {
    super(state, "context");
  }
}

export function targetFromTreeItem(value: unknown): IntelligenceTarget | undefined {
  if (isTarget(value)) return value;
  if (value instanceof SyndraItem) {
    const treeValue = value.value as IntelligenceTreeValue | undefined;
    return treeValue?.kind === "intelligence-entry"
      ? treeValue.entry.target
      : undefined;
  }
  return undefined;
}

export function workspaceFromTreeItem(
  value: unknown
): IntelligenceWorkspaceState | undefined {
  if (!(value instanceof SyndraItem)) return undefined;
  const treeValue = value.value as IntelligenceTreeValue | undefined;
  return treeValue?.kind === "intelligence-workspace"
    ? treeValue.workspace
    : undefined;
}

function workspaceItem(
  workspace: IntelligenceWorkspaceState,
  view: IntelligenceViewKind
): SyndraItem {
  const item = new SyndraItem(
    workspace.folder.name,
    vscode.TreeItemCollapsibleState.Expanded,
    { kind: "intelligence-workspace", workspace, view } satisfies WorkspaceValue
  );
  item.description = workspace.status?.status.state ?? "unavailable";
  item.tooltip = workspace.error ?? workspace.status?.status.message ?? "";
  item.iconPath = new vscode.ThemeIcon(
    workspace.error
      ? "error"
      : workspace.status?.status.state === "current"
        ? "repo"
        : "warning"
  );
  item.contextValue = "agentbusIntelligenceWorkspace";
  return item;
}

function entryItem(entry: IntelligenceTreeEntry): SyndraItem {
  const hasChildren = Boolean(entry.children?.length);
  const item = new SyndraItem(
    entry.label,
    hasChildren
      ? vscode.TreeItemCollapsibleState.Collapsed
      : vscode.TreeItemCollapsibleState.None,
    { kind: "intelligence-entry", entry } satisfies EntryValue
  );
  item.description = entry.description;
  item.tooltip = entry.tooltip;
  item.iconPath = new vscode.ThemeIcon(entry.icon);
  item.contextValue = entry.contextValue;
  if (entry.target?.kind === "symbol") {
    item.command = {
      command: "agentbus.openRepositorySymbol",
      title: "Open Repository Symbol",
      arguments: [entry.target]
    };
  } else if (
    entry.target?.kind === "boundary" ||
    entry.target?.kind === "ownership"
  ) {
    item.command = {
      command: "agentbus.openArchitectureBoundary",
      title: "Open Architecture Boundary",
      arguments: [entry.target]
    };
  }
  return item;
}

function messageItem(message: string): SyndraItem {
  const item = new SyndraItem(
    message,
    vscode.TreeItemCollapsibleState.None
  );
  item.iconPath = new vscode.ThemeIcon("info");
  item.contextValue = "agentbusIntelligenceMessage";
  return item;
}

function isTarget(value: unknown): value is IntelligenceTarget {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<IntelligenceTarget>;
  return ["symbol", "boundary", "ownership"].includes(candidate.kind ?? "");
}
