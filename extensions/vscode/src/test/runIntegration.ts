import { spawnSync } from "node:child_process";
import {
  access,
  cp,
  mkdir,
  readdir,
  readFile,
  rm,
  writeFile
} from "node:fs/promises";
import { join, resolve } from "node:path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const source = resolve(__dirname, "..", "..");
  const repositoryRoot = resolve(source, "..", "..");
  const stagingRoot = process.platform === "win32" ? "C:\\tmp" : "/tmp";
  const sessionRoot = join(
    stagingRoot,
    `agentbus-vscode-integration-${process.pid}`
  );
  const extensionDevelopmentPath = join(sessionRoot, "extension");
  const workspacePath = join(sessionRoot, "workspace");
  const userDataPath = join(sessionRoot, "user-data");
  const extensionsPath = join(sessionRoot, "extensions");
  const registryPath = join(sessionRoot, "daemons.json");
  const configPath = join(sessionRoot, "config.json");
  const statePath = join(sessionRoot, "state.db");
  const runsPath = join(sessionRoot, "runs");
  const worktreesPath = join(sessionRoot, "worktrees");
  const mcpLifecyclePath = join(sessionRoot, "mcp-lifecycle");
  const artifactPath = join(sessionRoot, "artifacts");
  const mcpFixturePath = resolve(
    repositoryRoot,
    "tests",
    "fixtures",
    "mcp",
    "fake_server.py"
  );
  const repositoryFixturePath = resolve(
    repositoryRoot,
    "agentbus",
    "evaluation",
    "fixtures_data",
    "repository-intelligence-mixed"
  );
  const mcpPrivateMarker = "vscode-e2e-private-mcp-marker";
  const pythonPath = await findPython(repositoryRoot);
  await access(mcpFixturePath);
  await access(repositoryFixturePath);
  await mkdir(stagingRoot, { recursive: true });
  await cleanup([sessionRoot]);
  await mkdir(mcpLifecyclePath, { recursive: true });
  await mkdir(artifactPath, { recursive: true });
  await writeDaemonConfig({
    configPath,
    workspacePath,
    statePath,
    runsPath,
    worktreesPath,
    mcpFixturePath,
    mcpLifecyclePath,
    mcpPrivateMarker
  });
  await cp(source, extensionDevelopmentPath, {
    recursive: true,
    filter: (path) =>
      !path.includes("node_modules") &&
      !path.includes(".vscode-test") &&
      !path.endsWith(".vsix")
  });
  await initializeRepository(workspacePath, repositoryFixturePath);
  const electronRunAsNode = process.env.ELECTRON_RUN_AS_NODE;
  const removedSecrets = removeProviderSecrets();
  let integrationCompleted = false;
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
        AGENTBUS_E2E_CONFIG: configPath,
        AGENTBUS_E2E_MCP_MARKER: mcpPrivateMarker,
        AGENTBUS_E2E_REGISTRY: registryPath,
        AGENTBUS_E2E_WORKSPACE: workspacePath,
        AGENTBUS_E2E_ARTIFACT_ROOT: artifactPath
      },
      launchArgs: [
        workspacePath,
        `--user-data-dir=${userDataPath}`,
        `--extensions-dir=${extensionsPath}`,
        "--disable-workspace-trust"
      ]
    });
    integrationCompleted = true;
  } finally {
    let runtimeCleanupVerified = false;
    try {
      await stopDaemons(pythonPath, registryPath);
      await verifyCleanup(
        registryPath,
        mcpLifecyclePath,
        integrationCompleted
      );
      runtimeCleanupVerified = true;
    } finally {
      if (electronRunAsNode !== undefined) {
        process.env.ELECTRON_RUN_AS_NODE = electronRunAsNode;
      }
      restoreEnvironment(removedSecrets);
      const cleanupPaths = runtimeCleanupVerified
        ? [sessionRoot]
        : [
            extensionDevelopmentPath,
            userDataPath,
            extensionsPath,
            artifactPath
          ];
      await cleanup(cleanupPaths);
    }
  }
}

async function initializeRepository(
  workspacePath: string,
  repositoryFixturePath: string
): Promise<void> {
  await cp(repositoryFixturePath, workspacePath, { recursive: true });
  git(workspacePath, "init");
  git(workspacePath, "config", "user.email", "vscode-e2e@agentbus.invalid");
  git(workspacePath, "config", "user.name", "AgentBus VS Code E2E");
  await writeFile(
    join(workspacePath, "README.md"),
    "# AgentBus VS Code integration workspace\n",
    "utf8"
  );
  await writeFile(
    join(workspacePath, "pyproject.toml"),
    "[project]\nname = \"agentbus-vscode-integration\"\nversion = \"0.1.0\"\n",
    "utf8"
  );
  await writeFile(
    join(workspacePath, "test_acceptance_tool.py"),
    "from acceptance_tool import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    "utf8"
  );
  await writeFile(
    join(workspacePath, "delete_me.txt"),
    "deterministic deletion target\n",
    "utf8"
  );
  git(workspacePath, "add", "--all");
  git(workspacePath, "commit", "-m", "initial");
}

interface DaemonConfigPaths {
  configPath: string;
  workspacePath: string;
  statePath: string;
  runsPath: string;
  worktreesPath: string;
  mcpFixturePath: string;
  mcpLifecyclePath: string;
  mcpPrivateMarker: string;
}

async function writeDaemonConfig(paths: DaemonConfigPaths): Promise<void> {
  const mcpCapabilities = ["mcp.connect", "mcp.invoke"].map((name) => ({
    name,
    scope: { mcp_servers: ["fixture"] }
  }));
  await writeFile(
    paths.configPath,
    JSON.stringify(
      {
        agentbus: {
          workspace_dir: paths.workspacePath,
          state_db: paths.statePath,
          runs_dir: paths.runsPath,
          worktree_root: paths.worktreesPath,
          mcp_server_configs: [
            {
              server_id: "fixture",
              transport: "stdio",
              executable_alias: "python",
              arguments: [
                "-u",
                paths.mcpFixturePath,
                "--mode",
                "normal",
                "--lifecycle-dir",
                paths.mcpLifecyclePath
              ],
              environment: { CI: paths.mcpPrivateMarker },
              capability_map: {
                echo: mcpCapabilities,
                write_note: mcpCapabilities
              }
            }
          ]
        }
      },
      null,
      2
    ),
    "utf8"
  );
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

async function stopDaemons(
  pythonPath: string,
  registryPath: string
): Promise<void> {
  runDaemonCommand(pythonPath, registryPath, "cleanup-stale");
  for (const daemonId of await registeredDaemonIds(registryPath)) {
    const result = daemonCommand(pythonPath, registryPath, "stop", daemonId);
    if (result.error || result.status !== 0) {
      runDaemonCommand(pythonPath, registryPath, "cleanup-stale");
      if ((await registeredDaemonIds(registryPath)).includes(daemonId)) {
        throw new Error("VS Code Electron daemon stop was not confirmed.");
      }
    }
  }
  runDaemonCommand(pythonPath, registryPath, "cleanup-stale");
}

function runDaemonCommand(
  pythonPath: string,
  registryPath: string,
  ...arguments_: string[]
): void {
  const result = daemonCommand(pythonPath, registryPath, ...arguments_);
  if (result.error || result.status !== 0) {
    throw new Error("VS Code Electron daemon cleanup command failed.");
  }
}

function daemonCommand(
  pythonPath: string,
  registryPath: string,
  ...arguments_: string[]
) {
  return spawnSync(
    pythonPath,
    [
      "-m",
      "agentbus.cli",
      "daemon",
      "--registry-path",
      registryPath,
      ...arguments_
    ],
    { encoding: "utf8", shell: false, timeout: 15_000 }
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

async function verifyCleanup(
  registryPath: string,
  mcpLifecyclePath: string,
  requireMcpLifecycle: boolean
): Promise<void> {
  if ((await registeredDaemonIds(registryPath)).length !== 0) {
    throw new Error("VS Code Electron test left a daemon registration behind.");
  }
  const names = await readdir(mcpLifecyclePath);
  const started = new Set(
    names
      .filter((name) => name.endsWith(".started"))
      .map((name) => name.slice(0, -".started".length))
  );
  const stopped = new Set(
    names
      .filter((name) => name.endsWith(".stopped"))
      .map((name) => name.slice(0, -".stopped".length))
  );
  if (
    (requireMcpLifecycle && started.size === 0) ||
    started.size !== stopped.size ||
    [...started].some((name) => !stopped.has(name))
  ) {
    throw new Error(
      `VS Code Electron MCP cleanup mismatch: ${started.size} started, ${stopped.size} stopped.`
    );
  }
}

async function registeredDaemonIds(registryPath: string): Promise<string[]> {
  let raw: string;
  try {
    raw = await readFile(registryPath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  const payload = JSON.parse(raw) as { daemons?: unknown };
  if (!Array.isArray(payload.daemons)) {
    throw new Error("VS Code Electron daemon registry is malformed.");
  }
  return payload.daemons.map((value) => {
    const daemonId =
      typeof value === "object" && value !== null && "daemon_id" in value
        ? (value as { daemon_id?: unknown }).daemon_id
        : undefined;
    if (typeof daemonId !== "string" || !/^[0-9a-f]{32}$/.test(daemonId)) {
      throw new Error("VS Code Electron daemon registry ID is invalid.");
    }
    return daemonId;
  });
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
