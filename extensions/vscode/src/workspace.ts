import { realpath } from "node:fs/promises";
import * as vscode from "vscode";
import { requireWorkspaceTrust } from "./workspaceTrust";
import {
  chooseWorkspace,
  type WorkspaceChoice,
  type WorkspaceChoicePicker
} from "./workspaceSelection";

export { isSafeRepositoryPath } from "./repositoryPath";

export async function ensureWorkspaceTrust(
  operation = "execution"
): Promise<boolean> {
  return requireWorkspaceTrust(
    {
      isTrusted: vscode.workspace.isTrusted,
      showWarning: async (message, action) =>
        vscode.window.showWarningMessage(message, action),
      manageTrust: async () => {
        await vscode.commands.executeCommand("workbench.trust.manage");
      }
    },
    operation
  );
}

export async function selectWorkspace(
  requireTrust: boolean,
  picker?: WorkspaceChoicePicker<vscode.WorkspaceFolder>
): Promise<vscode.WorkspaceFolder | undefined> {
  if (requireTrust && !(await ensureWorkspaceTrust())) {
    return undefined;
  }
  const folders = vscode.workspace.workspaceFolders ?? [];
  if (folders.length === 0) {
    void vscode.window.showErrorMessage(
      "Open a repository folder before using AgentBus."
    );
    return undefined;
  }
  const choices: WorkspaceChoice<vscode.WorkspaceFolder>[] = folders.map(
    (folder) => ({
      label: folder.name,
      description: folder.uri.fsPath,
      folder
    })
  );
  return chooseWorkspace(
    choices,
    picker ?? ((items) => vscode.window.showQuickPick(items, {
      title: "Select the repository AgentBus may operate on",
      placeHolder: "AgentBus never silently selects another workspace root"
    }))
  );
}

export async function canonicalWorkspacePath(
  folder: vscode.WorkspaceFolder
): Promise<string> {
  const canonical = await realpath(folder.uri.fsPath);
  const matches: vscode.WorkspaceFolder[] = [];
  for (const candidate of vscode.workspace.workspaceFolders ?? []) {
    let candidateCanonical: string;
    try {
      candidateCanonical = await realpath(candidate.uri.fsPath);
    } catch (error) {
      if (candidate.index === folder.index) throw error;
      continue;
    }
    if (pathKey(candidateCanonical) === pathKey(canonical)) {
      matches.push(candidate);
    }
  }
  if (matches.length !== 1 || matches[0]?.index !== folder.index) {
    throw new Error(
      "Selected repository does not resolve to the selected VS Code workspace folder."
    );
  }
  return canonical;
}

function pathKey(value: string): string {
  return process.platform === "win32" ? value.toLowerCase() : value;
}
