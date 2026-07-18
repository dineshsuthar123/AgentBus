import { spawnSync } from "node:child_process";
import { access, cp, mkdir, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const source = resolve(__dirname, "..", "..");
  const repositoryRoot = resolve(source, "..", "..");
  const stagingRoot = process.platform === "win32" ? "C:\\tmp" : "/tmp";
  const extensionDevelopmentPath = join(
    stagingRoot,
    "agentbus-vscode-integration"
  );
  const workspacePath = join(stagingRoot, "agentbus-vscode-workspace");
  const userDataPath = join(stagingRoot, "agentbus-vscode-user");
  const extensionsPath = join(stagingRoot, "agentbus-vscode-extensions");
  const registryPath = join(stagingRoot, "agentbus-vscode-daemons.json");
  const pythonPath = await findPython(repositoryRoot);
  await mkdir(stagingRoot, { recursive: true });
  await cleanup([
    extensionDevelopmentPath,
    workspacePath,
    userDataPath,
    extensionsPath,
    registryPath
  ]);
  await cp(source, extensionDevelopmentPath, {
    recursive: true,
    filter: (path) =>
      !path.includes("node_modules") &&
      !path.includes(".vscode-test") &&
      !path.endsWith(".vsix")
  });
  await initializeRepository(workspacePath);
  const electronRunAsNode = process.env.ELECTRON_RUN_AS_NODE;
  const removedSecrets = removeProviderSecrets();
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
      extensionTestsEnv: {
        AGENTBUS_E2E_PYTHON: pythonPath,
        AGENTBUS_E2E_REGISTRY: registryPath,
        AGENTBUS_E2E_WORKSPACE: workspacePath
      },
      launchArgs: [
        workspacePath,
        `--user-data-dir=${userDataPath}`,
        `--extensions-dir=${extensionsPath}`,
        "--disable-workspace-trust"
      ]
    });
  } finally {
    stopDaemon(pythonPath, registryPath);
    if (electronRunAsNode !== undefined) {
      process.env.ELECTRON_RUN_AS_NODE = electronRunAsNode;
    }
    restoreEnvironment(removedSecrets);
    await cleanup([
      extensionDevelopmentPath,
      workspacePath,
      userDataPath,
      extensionsPath,
      registryPath
    ]);
  }
}

async function initializeRepository(workspacePath: string): Promise<void> {
  await mkdir(workspacePath, { recursive: true });
  git(workspacePath, "init");
  git(workspacePath, "config", "user.email", "vscode-e2e@agentbus.invalid");
  git(workspacePath, "config", "user.name", "AgentBus VS Code E2E");
  await writeFile(
    join(workspacePath, "README.md"),
    "# AgentBus VS Code integration workspace\n",
    "utf8"
  );
  git(workspacePath, "add", "README.md");
  git(workspacePath, "commit", "-m", "initial");
}

async function findPython(repositoryRoot: string): Promise<string> {
  const local = resolve(
    repositoryRoot,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python"
  );
  try {
    await access(local);
    return local;
  } catch {
    const command = process.platform === "win32" ? "where.exe" : "which";
    const located = spawnSync(command, ["python"], {
      encoding: "utf8",
      shell: false
    });
    const first = located.stdout
      ?.split(/\r?\n/)
      .map((value) => value.trim())
      .find(Boolean);
    if (located.status !== 0 || !first) {
      throw new Error("Unable to locate Python for VS Code integration.");
    }
    return resolve(first);
  }
}

function git(workspacePath: string, ...arguments_: string[]): void {
  const result = spawnSync("git", arguments_, {
    cwd: workspacePath,
    encoding: "utf8",
    shell: false
  });
  if (result.status !== 0) {
    throw new Error(result.stderr || "Git command failed.");
  }
}

function stopDaemon(pythonPath: string, registryPath: string): void {
  spawnSync(
    pythonPath,
    [
      "-m",
      "agentbus.cli",
      "daemon",
      "--registry-path",
      registryPath,
      "stop"
    ],
    {
      encoding: "utf8",
      shell: false,
      timeout: 15_000
    }
  );
}

function removeProviderSecrets(): Map<string, string> {
  const removed = new Map<string, string>();
  const names = [
    "AGENTBUS_AZURE_API_KEY",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_OPENAI_API_KEY",
    "AZURE_TENANT_ID",
    "OPENAI_API_KEY"
  ];
  for (const name of names) {
    const value = process.env[name];
    if (value !== undefined) {
      removed.set(name, value);
      delete process.env[name];
    }
  }
  return removed;
}

function restoreEnvironment(values: Map<string, string>): void {
  for (const [name, value] of values) {
    process.env[name] = value;
  }
}

async function cleanup(paths: string[]): Promise<void> {
  for (const path of paths) {
    await rm(path, { recursive: true, force: true, maxRetries: 5 });
  }
}

void main().catch((error: unknown) => {
  process.stderr.write(`${String(error)}\n`);
  process.exitCode = 1;
});
