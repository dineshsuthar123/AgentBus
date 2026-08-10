import { spawnSync } from "node:child_process";
import {
  access,
  copyFile,
  mkdir,
  readFile,
  readdir,
  rm,
  stat,
  writeFile
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { runTests } from "@vscode/test-electron";
import JSZip from "jszip";

const SESSION_PREFIX = "agentbus-vscode-fresh-profile-";

async function main(): Promise<void> {
  const extensionSource = resolve(__dirname, "..", "..");
  const repositoryRoot = resolve(extensionSource, "..", "..");
  const stagingRoot = resolve(
    process.platform === "win32"
      ? process.env.TEMP ?? process.env.TMP ?? tmpdir()
      : tmpdir()
  );
  const sessionRoot = join(stagingRoot, `${SESSION_PREFIX}${process.pid}`);
  assertOwnedSession(sessionRoot, stagingRoot);
  const extensionPath = join(sessionRoot, "extension");
  const workspacePath = join(sessionRoot, "workspace");
  const userDataPath = join(sessionRoot, "user-data");
  const extensionsPath = join(sessionRoot, "extensions");
  const registryPath = join(sessionRoot, "daemons.json");
  const configPath = join(sessionRoot, "config.json");
  const statePath = join(sessionRoot, "state.db");
  const runsPath = join(sessionRoot, "runs");
  const worktreesPath = join(sessionRoot, "worktrees");
  const handoffPath = join(sessionRoot, "handoff.json");
  const vsixPath = join(extensionSource, "agentbus-vscode.vsix");
  const pythonPath = await findPython(repositoryRoot);
  await mkdir(stagingRoot, { recursive: true });
  await stopDaemons(pythonPath, registryPath);
  await assertNoRegisteredDaemons(registryPath);
  await removeOwnedSession(sessionRoot, stagingRoot);
  await mkdir(sessionRoot, { recursive: true });
  await unpackVsix(vsixPath, extensionPath);
  const extensionTestsPath = join(
    extensionPath,
    "out",
    "test",
    "freshProfileSuite",
    "index.js"
  );
  await mkdir(dirname(extensionTestsPath), { recursive: true });
  await copyFile(
    join(extensionSource, "out", "test", "freshProfileSuite", "index.js"),
    extensionTestsPath
  );
  await initializeRepository(workspacePath);
  await writeConfiguration({
    configPath,
    workspacePath,
    statePath,
    runsPath,
    worktreesPath
  });

  const electronRunAsNode = process.env.ELECTRON_RUN_AS_NODE;
  const removedSecrets = removeProviderSecrets();
  delete process.env.ELECTRON_RUN_AS_NODE;
  try {
    for (const stage of ["initial", "recovery"] as const) {
      try {
        await runTests({
          version: "1.96.4",
          extensionDevelopmentPath: extensionPath,
          extensionTestsPath,
          extensionTestsEnv: {
            AGENTBUS_FRESH_STAGE: stage,
            AGENTBUS_FRESH_PYTHON: pythonPath,
            AGENTBUS_FRESH_CONFIG: configPath,
            AGENTBUS_FRESH_REGISTRY: registryPath,
            AGENTBUS_FRESH_WORKSPACE: workspacePath,
            AGENTBUS_FRESH_HANDOFF: handoffPath
          },
          launchArgs: [
            workspacePath,
            `--user-data-dir=${userDataPath}`,
            `--extensions-dir=${extensionsPath}`,
            "--disable-workspace-trust"
          ]
        });
      } catch (error) {
        await emitElectronDiagnostics(userDataPath, stage);
        throw error;
      }
      await assertNoRegisteredDaemons(registryPath);
    }
    await assertNoWorktrees(worktreesPath);
    await assertFreshArtifacts(configPath, registryPath, handoffPath);
  } finally {
    let cleanupVerified = false;
    try {
      await stopDaemons(pythonPath, registryPath);
      await assertNoRegisteredDaemons(registryPath);
      cleanupVerified = true;
    } finally {
      if (electronRunAsNode !== undefined) {
        process.env.ELECTRON_RUN_AS_NODE = electronRunAsNode;
      }
      restoreEnvironment(removedSecrets);
      if (cleanupVerified) {
        await removeOwnedSession(sessionRoot, stagingRoot);
      }
    }
  }
}

interface ConfigurationPaths {
  configPath: string;
  workspacePath: string;
  statePath: string;
  runsPath: string;
  worktreesPath: string;
}

async function writeConfiguration(paths: ConfigurationPaths): Promise<void> {
  await writeFile(
    paths.configPath,
    JSON.stringify(
      {
        agentbus: {
          workspace_dir: paths.workspacePath,
          state_db: paths.statePath,
          runs_dir: paths.runsPath,
          worktree_root: paths.worktreesPath,
          provider_name: "deterministic",
          durable_execution: true,
          parallel_execution: false,
          max_workers: 1,
          keep_worktrees: false,
          repository_intelligence: true,
          log_level: "error"
        }
      },
      null,
      2
    ),
    "utf8"
  );
}

async function initializeRepository(workspacePath: string): Promise<void> {
  await mkdir(workspacePath, { recursive: true });
  await writeFile(
    join(workspacePath, "README.md"),
    "# Fresh AgentBus product acceptance\n",
    "utf8"
  );
  await writeFile(
    join(workspacePath, "pyproject.toml"),
    "[project]\nname = \"agentbus-fresh-profile\"\nversion = \"0.1.0\"\n",
    "utf8"
  );
  git(workspacePath, "init");
  git(workspacePath, "config", "user.email", "fresh-profile@agentbus.invalid");
  git(workspacePath, "config", "user.name", "AgentBus Fresh Profile");
  git(workspacePath, "add", "README.md", "pyproject.toml");
  git(workspacePath, "commit", "-m", "initial");
}

async function unpackVsix(vsixPath: string, extensionPath: string): Promise<void> {
  await access(vsixPath);
  const archive = await JSZip.loadAsync(await readFile(vsixPath));
  let files = 0;
  let bytes = 0;
  for (const entry of Object.values(archive.files)) {
    if (!entry.name.startsWith("extension/") || entry.dir) continue;
    const name = entry.name.slice("extension/".length);
    if (!safeArchivePath(name)) {
      throw new Error(`Fresh-profile VSIX contains an unsafe path: ${entry.name}`);
    }
    if (
      typeof entry.unixPermissions === "number" &&
      (entry.unixPermissions & 0o170000) === 0o120000
    ) {
      throw new Error(`Fresh-profile VSIX contains a symbolic link: ${entry.name}`);
    }
    const target = resolve(extensionPath, name);
    if (!target.startsWith(`${resolve(extensionPath)}${sep}`)) {
      throw new Error("Fresh-profile VSIX extraction escaped its owned directory.");
    }
    const content = await entry.async("nodebuffer");
    files += 1;
    bytes += content.length;
    if (files > 1_000 || bytes > 10 * 1_024 * 1_024) {
      throw new Error("Fresh-profile VSIX exceeded extraction limits.");
    }
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, content);
  }
  if (files === 0) throw new Error("Fresh-profile VSIX contained no extension files.");
  await access(join(extensionPath, "package.json"));
  await access(join(extensionPath, "out", "extension.js"));
}

function safeArchivePath(name: string): boolean {
  return Boolean(name) &&
    !name.includes("\\") &&
    !name.includes(":") &&
    ![...name].some((character) => character.charCodeAt(0) < 32) &&
    !isAbsolute(name) &&
    name.split("/").every((part) => part !== "" && part !== "." && part !== "..");
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
      ?.split(/\r?\n/u)
      .map((value) => value.trim())
      .find(Boolean);
    if (located.status !== 0 || !first) {
      throw new Error("Unable to locate Python for fresh-profile acceptance.");
    }
    return resolve(first);
  }
}

function git(workspacePath: string, ...args: string[]): void {
  const result = spawnSync("git", args, {
    cwd: workspacePath,
    encoding: "utf8",
    shell: false
  });
  if (result.status !== 0) throw new Error(result.stderr || "Git command failed.");
}

async function stopDaemons(pythonPath: string, registryPath: string): Promise<void> {
  runDaemonCommand(pythonPath, registryPath, "cleanup-stale");
  for (const daemonId of await registeredDaemonIds(registryPath)) {
    const stopped = daemonCommand(pythonPath, registryPath, "stop", daemonId);
    if (stopped.error || stopped.status !== 0) {
      runDaemonCommand(pythonPath, registryPath, "cleanup-stale");
      if ((await registeredDaemonIds(registryPath)).includes(daemonId)) {
        throw new Error("Fresh-profile daemon shutdown was not confirmed.");
      }
    }
  }
  runDaemonCommand(pythonPath, registryPath, "cleanup-stale");
}

function runDaemonCommand(
  pythonPath: string,
  registryPath: string,
  ...args: string[]
): void {
  const result = daemonCommand(pythonPath, registryPath, ...args);
  if (result.error || result.status !== 0) {
    throw new Error("Fresh-profile daemon cleanup command failed.");
  }
}

function daemonCommand(
  pythonPath: string,
  registryPath: string,
  ...args: string[]
) {
  return spawnSync(
    pythonPath,
    [
      "-m",
      "agentbus.cli",
      "daemon",
      "--registry-path",
      registryPath,
      ...args
    ],
    { encoding: "utf8", shell: false, timeout: 15_000 }
  );
}

async function assertNoRegisteredDaemons(registryPath: string): Promise<void> {
  const ids = await registeredDaemonIds(registryPath);
  if (ids.length > 0) {
    throw new Error(`Fresh-profile acceptance leaked ${ids.length} daemon(s).`);
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
  const value = JSON.parse(raw) as { daemons?: unknown };
  if (!Array.isArray(value.daemons)) {
    throw new Error("Fresh-profile daemon registry is malformed.");
  }
  return value.daemons.map((entry) => {
    const id = typeof entry === "object" && entry !== null && "daemon_id" in entry
      ? (entry as { daemon_id?: unknown }).daemon_id
      : undefined;
    if (typeof id !== "string" || !/^[0-9a-f]{32}$/u.test(id)) {
      throw new Error("Fresh-profile daemon registry ID is invalid.");
    }
    return id;
  });
}

async function assertNoWorktrees(worktreesPath: string): Promise<void> {
  try {
    const entries = await readdir(worktreesPath);
    if (entries.length > 0) {
      throw new Error(`Fresh-profile acceptance leaked worktrees: ${entries.join(", ")}`);
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
}

async function assertFreshArtifacts(
  configPath: string,
  registryPath: string,
  handoffPath: string
): Promise<void> {
  const combined = [configPath, registryPath, handoffPath]
    .map(async (path) => {
      try {
        return await readFile(path, "utf8");
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return "";
        throw error;
      }
    });
  const text = (await Promise.all(combined)).join("\n");
  if (/bearer_token|azure_openai_api_key\s*[=:]\s*(?!null)/iu.test(text)) {
    throw new Error("Fresh-profile artifacts contain credential-shaped data.");
  }
}

async function emitElectronDiagnostics(
  userDataPath: string,
  stage: string
): Promise<void> {
  const logs = await collectLogs(join(userDataPath, "logs"), 0);
  const selected = logs.filter((path) =>
    ["AgentBus.log", "exthost.log", "renderer.log"].includes(basename(path))
  ).slice(-4);
  for (const path of selected) {
    const content = await readFile(path, "utf8");
    const bounded = content.slice(-8_000).replace(
      /(Bearer\s+|api[_-]?key\s*[:=]\s*|password\s*[:=]\s*|token\s*[:=]\s*)[^\s,;]+/giu,
      "$1[REDACTED]"
    );
    process.stderr.write(
      `Fresh-profile ${stage} diagnostic ${basename(path)}:\n${bounded}\n`
    );
  }
}

async function collectLogs(root: string, depth: number): Promise<string[]> {
  if (depth > 5) return [];
  let entries;
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  const found: string[] = [];
  for (const entry of entries) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) {
      found.push(...await collectLogs(path, depth + 1));
    } else if (entry.isFile() && entry.name.endsWith(".log")) {
      found.push(path);
    }
  }
  return found.sort();
}

function removeProviderSecrets(): Map<string, string> {
  const removed = new Map<string, string>();
  for (const name of [
    "AGENTBUS_AZURE_API_KEY",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_OPENAI_API_KEY",
    "AZURE_TENANT_ID",
    "OPENAI_API_KEY"
  ]) {
    const value = process.env[name];
    if (value !== undefined) {
      removed.set(name, value);
      delete process.env[name];
    }
  }
  return removed;
}

function restoreEnvironment(values: Map<string, string>): void {
  for (const [name, value] of values) process.env[name] = value;
}

function assertOwnedSession(sessionRoot: string, stagingRoot: string): void {
  const resolvedRoot = resolve(stagingRoot);
  const resolvedSession = resolve(sessionRoot);
  const child = relative(resolvedRoot, resolvedSession);
  if (
    !child ||
    child.startsWith("..") ||
    isAbsolute(child) ||
    !basename(resolvedSession).startsWith(SESSION_PREFIX)
  ) {
    throw new Error("Refusing to manage a non-owned fresh-profile directory.");
  }
}

async function removeOwnedSession(
  sessionRoot: string,
  stagingRoot: string
): Promise<void> {
  assertOwnedSession(sessionRoot, stagingRoot);
  await rm(sessionRoot, { recursive: true, force: true, maxRetries: 5 });
  try {
    await stat(sessionRoot);
    throw new Error("Fresh-profile directory cleanup was not confirmed.");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
}

void main().catch((error: unknown) => {
  process.stderr.write(`${String(error)}\n`);
  process.exitCode = 1;
});
