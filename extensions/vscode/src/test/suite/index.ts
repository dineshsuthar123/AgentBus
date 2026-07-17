import assert from "node:assert/strict";
import * as vscode from "vscode";

export async function run(): Promise<void> {
  const extension = vscode.extensions.getExtension("agentbus.agentbus-vscode");
  assert.ok(extension, "AgentBus extension was not discovered");
  await extension.activate();
  const commands = await vscode.commands.getCommands(true);
  assert.ok(commands.includes("agentbus.startTask"));
  assert.ok(commands.includes("agentbus.openChanges"));
  assert.ok(commands.includes("agentbus.runDoctor"));
}
