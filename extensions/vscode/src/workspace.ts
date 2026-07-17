import { realpath } from "node:fs/promises";
import * as vscode from "vscode";

export async function selectWorkspace(
  requireTrust: boolean
): Promise<vscode.WorkspaceFolder | undefined> {
  if (requireTrust && !vscode.workspace.isTrusted) {
    const choice = await vscode.window.showWarningMessage(
      "AgentBus execution requires a trusted workspace. Diagnostics remain available.",
      "Manage Workspace Trust"
    );
    if (choice === "Manage Workspace Trust") {
      await vscode.commands.executeCommand("workbench.trust.manage");
    }
    return undefined;
  }
  const folders = vscode.workspace.workspaceFolders ?? [];
  if (folders.length === 0) {
    void vscode.window.showErrorMessage(
      "Open a repository folder before using AgentBus."
    );
    return undefined;
  }
  if (folders.length === 1) {
    return folders[0];
  }
  const picked = await vscode.window.showQuickPick(
    folders.map((folder) => ({
      label: folder.name,
      description: folder.uri.fsPath,
      folder
    })),
    {
      title: "Select the repository AgentBus may operate on",
      placeHolder: "AgentBus never silently selects another workspace root"
    }
  );
  return picked?.folder;
}

export async function canonicalWorkspacePath(
  folder: vscode.WorkspaceFolder
): Promise<string> {
  const canonical = await realpath(folder.uri.fsPath);
  const configured = vscode.workspace.getWorkspaceFolder(
    vscode.Uri.file(canonical)
  );
  if (!configured || configured.index !== folder.index) {
    throw new Error(
      "Selected repository does not resolve to the selected VS Code workspace folder."
    );
  }
  return canonical;
}

export function isSafeRepositoryPath(value: string): boolean {
  const normalized = value.replaceAll("\\", "/");
  return Boolean(
    normalized &&
      !normalized.startsWith("/") &&
      !/^[A-Za-z]:/.test(normalized) &&
      !normalized.split("/").some((part) => part === ".." || part === "")
  );
}
