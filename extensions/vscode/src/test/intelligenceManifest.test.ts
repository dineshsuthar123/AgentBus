import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

interface ExtensionManifest {
  activationEvents: string[];
  contributes: {
    commands: Array<{ command: string; title: string }>;
    views: Record<string, Array<{ id: string; name: string }>>;
  };
}

const manifest = JSON.parse(
  readFileSync(join(__dirname, "..", "..", "package.json"), "utf8")
) as ExtensionManifest;

test("repository intelligence commands and native views are contributed", () => {
  const commands = new Set(
    manifest.contributes.commands.map((command) => command.command)
  );
  const requiredCommands = [
    "agentbus.buildRepositoryIndex",
    "agentbus.updateRepositoryIndex",
    "agentbus.verifyRepositoryIndex",
    "agentbus.repairRepositoryIndex",
    "agentbus.searchRepository",
    "agentbus.findRepositorySymbol",
    "agentbus.showRepositoryDependencies",
    "agentbus.showRepositoryDependents",
    "agentbus.analyzeChangeImpact",
    "agentbus.findRelevantTests",
    "agentbus.previewAgentContext",
    "agentbus.openArchitectureBoundary"
  ];
  for (const command of requiredCommands) assert.equal(commands.has(command), true);

  const viewIds = new Set(
    Object.values(manifest.contributes.views)
      .flat()
      .map((view) => view.id)
  );
  for (const view of [
    "agentbus.intelligence",
    "agentbus.symbols",
    "agentbus.impact",
    "agentbus.contextPlan"
  ]) {
    assert.equal(viewIds.has(view), true);
    assert.equal(manifest.activationEvents.includes(`onView:${view}`), true);
  }
});

test("repository intelligence manifest uses native views without webviews", () => {
  const serialized = JSON.stringify(manifest.contributes);
  assert.doesNotMatch(serialized, /webview|javascript:|https?:\/\//i);
});
