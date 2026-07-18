import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { readFile } from "node:fs/promises";
import type { ExtensionContext, OutputChannel } from "vscode";
import * as vscode from "vscode";
import { AgentBusClient } from "./apiClient";
import {
  buildLaunchSpec,
  buildStopSpec,
  daemonBaseUrl,
  parseReadyHandshake,
  parseRegistry,
  type LaunchSettings
} from "./daemonProtocol";
import { CONTROL_PROTOCOL_VERSION, type DaemonRegistryEntry } from "./generated/protocol";
import { redactText, safeError } from "./redaction";

const secretPrefix = "agentbus.daemonToken.";

export interface DaemonConnection {
  entry: DaemonRegistryEntry;
  client: AgentBusClient;
}

export class DaemonManager implements vscode.Disposable {
  private connection: DaemonConnection | undefined;
  private connecting: Promise<DaemonConnection> | undefined;
  private child: ChildProcessWithoutNullStreams | undefined;

  public constructor(
    private readonly context: ExtensionContext,
    private readonly output: OutputChannel
  ) {}

  public current(): DaemonConnection | undefined {
    return this.connection;
  }

  public async connectOrStart(): Promise<DaemonConnection> {
    if (this.connection) {
      return this.connection;
    }
    if (this.connecting) {
      return this.connecting;
    }
    const connecting = this.connectOrStartOnce();
    this.connecting = connecting;
    try {
      return await connecting;
    } finally {
      if (this.connecting === connecting) {
        this.connecting = undefined;
      }
    }
  }

  public async discover(): Promise<DaemonConnection | undefined> {
    const settings = this.settings();
    const registryPath = buildLaunchSpec(settings).registryPath;
    let content: string;
    try {
      content = await readFile(registryPath, "utf8");
    } catch {
      return undefined;
    }
    let registry;
    try {
      registry = parseRegistry(content);
    } catch (error) {
      this.output.appendLine(`Registry ignored: ${safeError(error)}`);
      return undefined;
    }
    for (const entry of [...registry.daemons].reverse()) {
      if (entry.protocol_version !== CONTROL_PROTOCOL_VERSION) {
        continue;
      }
      const token = await this.context.secrets.get(`${secretPrefix}${entry.daemon_id}`);
      if (!token) {
        continue;
      }
      try {
        const client = new AgentBusClient(daemonBaseUrl(entry), token);
        const info = await client.info();
        if (
          info.daemon_id !== entry.daemon_id ||
          info.protocol_version !== CONTROL_PROTOCOL_VERSION
        ) {
          continue;
        }
        this.connection = { entry, client };
        this.output.appendLine(`Connected to AgentBus daemon ${entry.daemon_id}.`);
        return this.connection;
      } catch (error) {
        this.output.appendLine(
          `Daemon ${entry.daemon_id} unavailable: ${safeError(error)}`
        );
      }
    }
    return undefined;
  }

  public async start(): Promise<DaemonConnection> {
    if (this.child && this.child.exitCode === null) {
      throw new Error("AgentBus daemon startup is already in progress.");
    }
    const spec = buildLaunchSpec(this.settings());
    const child = spawn(spec.command, spec.args, {
      cwd: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
      env: process.env,
      shell: false,
      windowsHide: true
    });
    this.child = child;
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (data: string) => {
      this.output.append(redactText(data));
    });
    const handshake = await readHandshake(child);
    await this.context.secrets.store(
      `${secretPrefix}${handshake.daemon_id}`,
      handshake.bearer_token
    );
    const entry: DaemonRegistryEntry = {
      daemon_id: handshake.daemon_id,
      pid: handshake.pid,
      executable: spec.command,
      process_start_identity: "",
      host: handshake.host,
      port: handshake.port,
      protocol_version: handshake.protocol_version,
      agentbus_version: handshake.agentbus_version,
      started_at: new Date().toISOString(),
      heartbeat_at: new Date().toISOString(),
      state_database: "",
      registry_path: handshake.registry_path
    };
    const client = new AgentBusClient(
      daemonBaseUrl(entry),
      handshake.bearer_token
    );
    const info = await client.info();
    if (
      info.daemon_id !== handshake.daemon_id ||
      info.protocol_version !== CONTROL_PROTOCOL_VERSION
    ) {
      await this.context.secrets.delete(`${secretPrefix}${handshake.daemon_id}`);
      child.kill();
      throw new Error("Started AgentBus daemon failed protocol identity validation.");
    }
    this.connection = { entry, client };
    child.stdout.on("data", (data: Buffer) => {
      this.output.append(redactText(data.toString("utf8")));
    });
    child.on("exit", () => {
      if (this.connection?.entry.daemon_id === handshake.daemon_id) {
        this.connection = undefined;
      }
    });
    this.output.appendLine(`Started AgentBus daemon ${handshake.daemon_id}.`);
    return this.connection;
  }

  public async stop(): Promise<void> {
    const connection = this.connection;
    if (!connection) {
      return;
    }
    const spec = buildStopSpec(this.settings(), connection.entry.daemon_id);
    const result = await runChild(spec.command, spec.args);
    if (result.exitCode !== 0) {
      throw new Error(
        `AgentBus refused the safe daemon stop request: ${
          redactText(result.stderr) || "no diagnostic was returned"
        }`
      );
    }
    await this.context.secrets.delete(
      `${secretPrefix}${connection.entry.daemon_id}`
    );
    this.connection = undefined;
  }

  public async restart(): Promise<DaemonConnection> {
    await this.stop();
    return this.start();
  }

  public dispose(): void {
    this.connecting = undefined;
    this.child = undefined;
    this.connection = undefined;
  }

  private async connectOrStartOnce(): Promise<DaemonConnection> {
    const existing = await this.discover();
    if (existing) {
      return existing;
    }
    const configuration = vscode.workspace.getConfiguration("agentbus");
    if (!configuration.get<boolean>("autoStartDaemon", true)) {
      throw new Error(
        "No compatible AgentBus daemon is available and automatic startup is disabled."
      );
    }
    return this.start();
  }

  private settings(): LaunchSettings {
    const configuration = vscode.workspace.getConfiguration("agentbus");
    return {
      executablePath: configuration.get<string>("executablePath") || undefined,
      pythonPath: configuration.get<string>("pythonPath") || undefined,
      registryPath: configuration.get<string>("registryPath") || undefined,
      logLevel: configuration.get<"error" | "warning" | "info">(
        "logLevel",
        "warning"
      )
    };
  }
}

async function readHandshake(
  child: ChildProcessWithoutNullStreams
): Promise<ReturnType<typeof parseReadyHandshake>> {
  child.stdout.setEncoding("utf8");
  return new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => {
      reject(new Error("Timed out waiting for AgentBus daemon startup."));
      child.kill();
    }, 15_000);
    const onData = (chunk: string): void => {
      buffer += chunk;
      if (buffer.length > 65_536) {
        cleanup();
        reject(new Error("AgentBus daemon startup output exceeded its limit."));
        child.kill();
        return;
      }
      const newline = buffer.indexOf("\n");
      if (newline < 0) {
        return;
      }
      cleanup();
      try {
        resolve(parseReadyHandshake(buffer.slice(0, newline)));
      } catch (error) {
        reject(error);
        child.kill();
      }
    };
    const onError = (error: Error): void => {
      cleanup();
      reject(error);
    };
    const onExit = (): void => {
      cleanup();
      reject(new Error("AgentBus daemon exited before startup completed."));
    };
    const cleanup = (): void => {
      clearTimeout(timer);
      child.stdout.off("data", onData);
      child.off("error", onError);
      child.off("exit", onExit);
    };
    child.stdout.on("data", onData);
    child.once("error", onError);
    child.once("exit", onExit);
  });
}

async function runChild(
  command: string,
  args: string[]
): Promise<{ exitCode: number; stderr: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      env: process.env,
      shell: false,
      windowsHide: true
    });
    let stderr = "";
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (value: string) => {
      if (stderr.length < 16_384) {
        stderr += value;
      }
    });
    child.once("error", reject);
    child.once("exit", (code) =>
      resolve({
        exitCode: code ?? 1,
        stderr: stderr.slice(0, 16_384).trim()
      })
    );
  });
}
