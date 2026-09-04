import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

interface Setting {
  default?: unknown;
  description?: string;
  scope?: string;
}

interface ProductManifest {
  name: string;
  displayName: string;
  publisher: string;
  syndraCompatibility: Record<string, unknown>;
  agentbusCompatibility: Record<string, unknown>;
  contributes: {
    commands: Array<{ command: string; title: string }>;
    configuration: { properties: Record<string, Setting> };
    walkthroughs: Array<{
      id: string;
      steps: Array<{
        completionEvents?: string[];
        description: string;
        media?: { image?: string; altText?: string };
      }>;
    }>;
  };
}

const manifest = JSON.parse(
  readFileSync(join(__dirname, "..", "..", "package.json"), "utf8")
) as ProductManifest;

test("manifest uses the Syndra product identity with mirrored legacy compatibility", () => {
  assert.equal(manifest.name, "syndra-vscode");
  assert.equal(manifest.displayName, "Syndra");
  assert.equal(manifest.publisher, "syndra");
  assert.deepEqual(manifest.agentbusCompatibility, manifest.syndraCompatibility);
  assert.equal(
    manifest.contributes.commands.every((command) =>
      command.title.startsWith("Syndra:")
    ),
    true
  );
});

test("product onboarding commands and native walkthrough are contributed", () => {
  const commands = new Set(
    manifest.contributes.commands.map((command) => command.command)
  );
  for (const command of [
    "agentbus.getStarted",
    "agentbus.runSetup",
    "agentbus.runQuickstart",
    "agentbus.checkInstallation",
    "agentbus.openDocumentation",
    "agentbus.openResolvedConfiguration"
  ]) {
    assert.equal(commands.has(command), true, command);
  }
  const walkthrough = manifest.contributes.walkthroughs.find(
    (candidate) => candidate.id === "agentbus.gettingStarted"
  );
  assert.ok(walkthrough);
  assert.equal(walkthrough.steps.length >= 5, true);
  assert.equal(
    walkthrough.steps.every((step) =>
      step.description.includes("command:agentbus.") &&
      step.media?.image === "media/syndra.svg" &&
      Boolean(step.media.altText) &&
      (step.completionEvents?.length ?? 0) > 0
    ),
    true
  );
});

test("settings use safe scopes descriptions and offline defaults", () => {
  const settings = manifest.contributes.configuration.properties;
  for (const [name, setting] of Object.entries(settings)) {
    assert.equal(Boolean(setting.description?.trim()), true, name);
    assert.equal(/(?:api.?key|password|credential|secret|token)$/iu.test(name), false);
  }
  for (const name of [
    "agentbus.executablePath",
    "agentbus.pythonPath",
    "agentbus.configPath",
    "agentbus.registryPath",
    "agentbus.showWelcomeOnStartup"
  ]) {
    assert.equal(settings[name]?.scope, "machine", name);
  }
  assert.equal(settings["agentbus.defaultProvider"]?.default, "deterministic");
});
