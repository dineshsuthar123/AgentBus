import { spawn, spawnSync } from "node:child_process";
import {
  access,
  copyFile,
  mkdir,
  realpath,
  readFile,
  readdir,
  rm,
  stat,
  writeFile
} from "node:fs/promises";
import { tmpdir } from "node:os";
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
  win32 as windowsPath
} from "node:path";
import { downloadAndUnzipVSCode } from "@vscode/test-electron";
import JSZip from "jszip";

const POSIX_SESSION_PREFIX = "agentbus-vscode-fresh-profile-";
const WINDOWS_SESSION_PREFIX = "agentbus-vfp-";
const INCOMPATIBLE_DAEMON_ID = "ffffffffffffffffffffffffffffffff";

type FreshStage =
  | "initial"
  | "recovery"
  | "stress"
  | "untrusted"
  | "trusted-recovery";

async function main(): Promise<void> {
  const extensionSource = resolve(__dirname, "..", "..");
  const repositoryRoot = resolve(extensionSource, "..", "..");
  const requestedStagingRoot = freshProfileStagingRoot(
    process.platform,
    tmpdir()
  );
  await mkdir(requestedStagingRoot, { recursive: true });
  const stagingRoot = await realpath(requestedStagingRoot);
  const sessionRoot = join(stagingRoot, freshProfileSessionName(process.pid));
  assertOwnedSession(sessionRoot, stagingRoot);
  const extensionPath = join(sessionRoot, "extension");
  const workspacePath = join(sessionRoot, "workspace");
  const secondaryWorkspacePath = join(sessionRoot, "workspace-secondary");
  const workspaceFilePath = join(sessionRoot, "stress.code-workspace");
  const lifecycleUserDataPath = join(sessionRoot, "user-data-lifecycle");
  const stressUserDataPath = join(sessionRoot, "user-data-stress");
  const trustUserDataPath = join(sessionRoot, "user-data-trust");
  const extensionsPath = join(sessionRoot, "extensions");
  const registryPath = join(sessionRoot, "daemons.json");
  const configPath = join(sessionRoot, "config.json");
  const mcpConfigPath = join(sessionRoot, "mcp-config.json");
  const statePath = join(sessionRoot, "state.db");
  const runsPath = join(sessionRoot, "runs");
  const worktreesPath = join(sessionRoot, "worktrees");
  const handoffPath = join(sessionRoot, "handoff.json");
  const vsixPath = join(extensionSource, "agentbus-vscode.vsix");
  const pythonPath = await findPython(repositoryRoot);
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
    "bootstrap.js"
  );
  await mkdir(dirname(extensionTestsPath), { recursive: true });
  await copyFile(
    join(extensionSource, "out", "test", "freshProfileSuite", "bootstrap.js"),
    extensionTestsPath
  );
  await copyFile(
    join(extensionSource, "out", "test", "freshProfileSuite", "index.js"),
    join(dirname(extensionTestsPath), "index.js")
  );
  await initializeRepository(workspacePath, "primary", true);
  await initializeRepository(secondaryWorkspacePath, "secondary", false);
  await writeWorkspaceFile(
    workspaceFilePath,
    workspacePath,
    secondaryWorkspacePath
  );
  await writeTrustSettings(trustUserDataPath);
  await writeConfiguration({
    configPath,
    workspacePath,
    statePath,
    runsPath,
    worktreesPath
  });
  await writeConfiguration(
    {
      configPath: mcpConfigPath,
      workspacePath,
      statePath,
      runsPath,
      worktreesPath
    },
    true
  );

  const electronRunAsNode = process.env.ELECTRON_RUN_AS_NODE;
  const removedSecrets = removeProviderSecrets();
  delete process.env.ELECTRON_RUN_AS_NODE;
  try {
    const vscodeExecutablePath = await downloadAndUnzipVSCode({
      version: "1.96.4",
      extensionDevelopmentPath: extensionPath
    });
    const stages: FreshStage[] = [
      "initial",
      "recovery",
      "stress",
      "untrusted",
      "trusted-recovery"
    ];
    for (const stage of stages) {
      process.stdout.write(`Fresh-profile stage ${stage}: starting\n`);
      if (stage === "stress") {
        await writeIncompatibleRegistry(registryPath, statePath);
      }
      const userDataPath = profilePath(stage, {
        lifecycle: lifecycleUserDataPath,
        stress: stressUserDataPath,
        trust: trustUserDataPath
      });
      try {
        await runExtensionHost({
          vscodeExecutablePath,
          extensionDevelopmentPath: extensionPath,
          extensionTestsPath,
          environment: {
            AGENTBUS_FRESH_STAGE: stage,
            AGENTBUS_FRESH_PYTHON: pythonPath,
            AGENTBUS_FRESH_CONFIG: configPath,
            AGENTBUS_FRESH_MCP_CONFIG: mcpConfigPath,
            AGENTBUS_FRESH_REGISTRY: registryPath,
            AGENTBUS_FRESH_WORKSPACE: workspacePath,
            AGENTBUS_FRESH_SECONDARY_WORKSPACE: secondaryWorkspacePath,
            AGENTBUS_FRESH_INCOMPATIBLE_DAEMON: INCOMPATIBLE_DAEMON_ID,
            AGENTBUS_FRESH_HANDOFF: handoffPath
          },
          launchTarget: stage === "stress" ? workspaceFilePath : workspacePath,
          userDataPath,
          extensionsPath,
          trusted: stage !== "untrusted",
          timeoutMs: stage === "stress" ? 300_000 : 180_000
        });
      } catch (error) {
        await emitElectronDiagnostics(userDataPath, stage);
        await emitAssertionDiagnostic(handoffPath, stage);
        throw error;
      }
      await assertNoRegisteredDaemons(registryPath);
      process.stdout.write(`Fresh-profile stage ${stage}: passed\n`);
    }
    await assertNoWorktrees(worktreesPath);
    await assertFreshArtifacts(
      configPath,
      mcpConfigPath,
      registryPath,
      handoffPath
    );
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

async function writeConfiguration(
  paths: ConfigurationPaths,
  includeSyntheticMcp = false
): Promise<void> {
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
          ...(includeSyntheticMcp
            ? { mcp_server_configs: [syntheticFailureMcpServer()] }
            : {}),
          log_level: "error"
        }
      },
      null,
      2
    ),
    "utf8"
  );
}

function syntheticFailureMcpServer(): object {
  const scope = { mcp_servers: ["synthetic-failure"] };
  return {
    server_id: "synthetic-failure",
    transport: "stdio",
    executable_alias: "python",
    arguments: ["-c", "raise SystemExit(7)"],
    startup_timeout_seconds: 1,
    request_timeout_seconds: 1,
    capability_map: {
      echo: [
        { name: "mcp.connect", scope },
        { name: "mcp.invoke", scope }
      ]
    }
  };
}

interface ExtensionHostOptions {
  vscodeExecutablePath: string;
  extensionDevelopmentPath: string;
  extensionTestsPath: string;
  environment: Record<string, string>;
  launchTarget: string;
  userDataPath: string;
  extensionsPath: string;
  trusted: boolean;
  timeoutMs: number;
}

async function runExtensionHost(options: ExtensionHostOptions): Promise<void> {
  const args = [
    options.launchTarget,
    `--user-data-dir=${options.userDataPath}`,
    `--extensions-dir=${options.extensionsPath}`,
    "--no-sandbox",
    "--disable-gpu-sandbox",
    "--disable-updates",
    "--skip-welcome",
    "--skip-release-notes",
    `--extensionTestsPath=${options.extensionTestsPath}`,
    `--extensionDevelopmentPath=${options.extensionDevelopmentPath}`
  ];
  if (options.trusted) args.push("--disable-workspace-trust");
  const child = spawn(options.vscodeExecutablePath, args, {
    detached: process.platform !== "win32",
    env: extensionHostEnvironment(options.environment),
    shell: false,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"]
  });
  let stdout = "";
  let stderr = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (data: string) => {
    stdout = boundedOutput(stdout, data);
  });
  child.stderr.on("data", (data: string) => {
    stderr = boundedOutput(stderr, data);
  });

  await new Promise<void>((resolveRun, rejectRun) => {
    let settled = false;
    const finish = (error?: Error): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) rejectRun(error);
      else resolveRun();
    };
    const timer = setTimeout(() => {
      terminateOwnedProcessTree(child.pid);
      finish(new Error("Fresh-profile Extension Host exceeded its time limit."));
    }, options.timeoutMs);
    child.once("error", (error) => finish(error));
    child.once("close", (code, signal) => {
      if (code === 0) {
        finish();
        return;
      }
      const detail = redactDiagnosticOutput(`${stderr}\n${stdout}`).trim();
      finish(
        new Error(
          `Fresh-profile Extension Host exited with ${code ?? signal ?? "unknown"}.${
            detail ? `\n${detail}` : ""
          }`
        )
      );
    });
  });
}

function extensionHostEnvironment(
  additions: Record<string, string>
): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = {};
  for (const [name, value] of Object.entries(process.env)) {
    if (
      value !== undefined &&
      name !== "ELECTRON_RUN_AS_NODE" &&
      !name.startsWith("VSCODE_")
    ) {
      environment[name] = value;
    }
  }
  return { ...environment, ...additions };
}

function boundedOutput(current: string, addition: string): string {
  return `${current}${addition}`.slice(-65_536);
}

function redactDiagnosticOutput(value: string): string {
  return value.slice(-16_000).replace(
    /(Bearer\s+|api[_-]?key\s*[:=]\s*|password\s*[:=]\s*|token\s*[:=]\s*)[^\s,;]+/giu,
    "$1[REDACTED]"
  );
}

function terminateOwnedProcessTree(pid: number | undefined): void {
  if (!pid) return;
  if (process.platform === "win32") {
    spawnSync("taskkill.exe", ["/pid", String(pid), "/t", "/f"], {
      encoding: "utf8",
      shell: false,
      windowsHide: true,
      timeout: 10_000
    });
    return;
  }
  try {
    process.kill(-pid, "SIGKILL");
  } catch {
    // The process group may have exited between the timeout and termination.
  }
}

async function initializeRepository(
  workspacePath: string,
  label: string,
  includeDeleteTarget: boolean
): Promise<void> {
  await mkdir(workspacePath, { recursive: true });
  await writeFile(
    join(workspacePath, "README.md"),
    `# Fresh AgentBus ${label} product acceptance\n`,
    "utf8"
  );
  await writeFile(
    join(workspacePath, "pyproject.toml"),
    "[project]\nname = \"agentbus-fresh-profile\"\nversion = \"0.1.0\"\n",
    "utf8"
  );
  if (includeDeleteTarget) {
    await writeFile(
      join(workspacePath, "delete_me.txt"),
      "deterministic deletion target\n",
      "utf8"
    );
  }
  git(workspacePath, "init");
  git(workspacePath, "config", "user.email", "fresh-profile@agentbus.invalid");
  git(workspacePath, "config", "user.name", "AgentBus Fresh Profile");
  git(workspacePath, "add", "--all");
  git(workspacePath, "commit", "-m", "initial");
}

async function writeWorkspaceFile(
  workspaceFilePath: string,
  primaryWorkspacePath: string,
  secondaryWorkspacePath: string
): Promise<void> {
  await writeFile(
    workspaceFilePath,
    JSON.stringify(
      {
        folders: [
          { name: "primary", path: primaryWorkspacePath },
          { name: "secondary", path: secondaryWorkspacePath }
        ],
        settings: {}
      },
      null,
      2
    ),
    "utf8"
  );
}

async function writeTrustSettings(userDataPath: string): Promise<void> {
  const userPath = join(userDataPath, "User");
  await mkdir(userPath, { recursive: true });
  await writeFile(
    join(userPath, "settings.json"),
    JSON.stringify(
      {
        "security.workspace.trust.enabled": true,
        "security.workspace.trust.startupPrompt": "never"
      },
      null,
      2
    ),
    "utf8"
  );
}

interface ProfilePaths {
  lifecycle: string;
  stress: string;
  trust: string;
}

function profilePath(stage: FreshStage, paths: ProfilePaths): string {
  if (stage === "initial" || stage === "recovery") return paths.lifecycle;
  if (stage === "stress") return paths.stress;
  return paths.trust;
}

async function writeIncompatibleRegistry(
  registryPath: string,
  statePath: string
): Promise<void> {
  const timestamp = new Date().toISOString();
  await writeFile(
    registryPath,
    JSON.stringify(
      {
        version: 1,
        daemons: [
          {
            daemon_id: INCOMPATIBLE_DAEMON_ID,
            pid: 2_147_483_647,
            executable: "incompatible-agentbus",
            process_start_identity: "synthetic-incompatible-daemon",
            host: "127.0.0.1",
            port: 1,
            protocol_version: "0.9",
            agentbus_version: "0.5.9",
            started_at: timestamp,
            heartbeat_at: timestamp,
            state_database: statePath,
            registry_path: registryPath,
            idle_timeout_seconds: 1
          }
        ]
      },
      null,
      2
    ),
    "utf8"
  );
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
  ...paths: string[]
): Promise<void> {
  const combined = paths.map(async (path) => {
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
    /AgentBus|exthost|extension|renderer|sharedprocess|window/iu.test(
      basename(path)
    )
  ).slice(-10);
  if (selected.length === 0) {
    process.stderr.write(
      `Fresh-profile ${stage} produced no selected Extension Host logs.\n`
    );
  }
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

async function emitAssertionDiagnostic(
  handoffPath: string,
  stage: string
): Promise<void> {
  try {
    const diagnostic = await readFile(`${handoffPath}.failure`, "utf8");
    process.stderr.write(
      `Fresh-profile ${stage} assertion: ${diagnostic.slice(0, 2_000)}\n`
    );
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
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
    !basename(resolvedSession).startsWith(
      freshProfileSessionPrefix(process.platform)
    )
  ) {
    throw new Error("Refusing to manage a non-owned fresh-profile directory.");
  }
}

export function freshProfileSessionName(
  pid: number,
  platform: NodeJS.Platform = process.platform
): string {
  if (!Number.isInteger(pid) || pid <= 0 || pid > 0x7fff_ffff) {
    throw new Error("Fresh-profile process ID is outside the supported range.");
  }
  const suffix = platform === "win32" ? pid.toString(36) : String(pid);
  return `${freshProfileSessionPrefix(platform)}${suffix}`;
}

export function freshProfileSessionPrefix(platform: NodeJS.Platform): string {
  // VS Code derives Windows IPC names from this path and exits before logging
  // when an otherwise normal per-user temporary path becomes too long.
  return platform === "win32" ? WINDOWS_SESSION_PREFIX : POSIX_SESSION_PREFIX;
}

export function freshProfileStagingRoot(
  platform: NodeJS.Platform,
  systemTempDirectory: string
): string {
  if (platform !== "win32") return resolve(systemTempDirectory);
  const driveRoot = windowsPath.parse(
    windowsPath.resolve(systemTempDirectory)
  ).root;
  if (!/^[a-z]:\\$/iu.test(driveRoot)) {
    throw new Error("Fresh-profile acceptance requires a local Windows drive.");
  }
  return windowsPath.join(driveRoot, "tmp");
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

if (require.main === module) {
  void main().catch((error: unknown) => {
    process.stderr.write(`${String(error)}\n`);
    process.exitCode = 1;
  });
}
