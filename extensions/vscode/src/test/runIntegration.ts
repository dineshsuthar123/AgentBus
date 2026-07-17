import { cp, mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const source = resolve(__dirname, "..", "..");
  const stagingRoot = process.platform === "win32" ? "C:\\tmp" : "/tmp";
  const extensionDevelopmentPath = join(stagingRoot, "agentbus-vscode-integration");
  await mkdir(stagingRoot, { recursive: true });
  await rm(extensionDevelopmentPath, { recursive: true, force: true });
  await cp(source, extensionDevelopmentPath, {
    recursive: true,
    filter: (path) =>
      !path.includes("node_modules") &&
      !path.includes(".vscode-test") &&
      !path.endsWith(".vsix")
  });
  const electronRunAsNode = process.env.ELECTRON_RUN_AS_NODE;
  delete process.env.ELECTRON_RUN_AS_NODE;
  try {
    await runTests({
      version: "1.96.4",
      extensionDevelopmentPath,
      extensionTestsPath: join(
        extensionDevelopmentPath,
        "out",
        "test",
        "suite",
        "index"
      ),
      launchArgs: [
        `--user-data-dir=${join(stagingRoot, "agentbus-vscode-user")}`,
        `--extensions-dir=${join(stagingRoot, "agentbus-vscode-extensions")}`,
        "--disable-workspace-trust"
      ]
    });
  } finally {
    if (electronRunAsNode !== undefined) {
      process.env.ELECTRON_RUN_AS_NODE = electronRunAsNode;
    }
    await rm(extensionDevelopmentPath, { recursive: true, force: true });
  }
}

void main().catch((error: unknown) => {
  process.stderr.write(`${String(error)}\n`);
  process.exitCode = 1;
});
