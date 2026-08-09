import { homedir } from "node:os";
import { join, resolve } from "node:path";
import type { DaemonRegistryEntry, ReadyHandshake } from "./generated/protocol";
import { assessDaemonCompatibility } from "./compatibility";

export interface RegistryDocument {
  version: number;
  daemons: DaemonRegistryEntry[];
}

export interface LaunchSettings {
  executablePath?: string;
  pythonPath?: string;
  configPath?: string;
  registryPath?: string;
  logLevel?: "error" | "warning" | "info";
}

export interface LaunchSpec {
  command: string;
  args: string[];
  registryPath: string;
}

export function parseReadyHandshake(line: string): ReadyHandshake {
  if (line.length > 65_536) {
    throw new Error("AgentBus startup handshake exceeded its size limit.");
  }
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    throw new Error("AgentBus did not return a valid startup handshake.");
  }
  if (!isRecord(value)) {
    throw new Error("AgentBus startup handshake is not an object.");
  }
  const requiredStrings = [
    "protocol_version",
    "host",
    "daemon_id",
    "agentbus_version",
    "registry_path",
    "bearer_token",
    "token_delivery"
  ] as const;
  for (const key of requiredStrings) {
    if (typeof value[key] !== "string" || value[key].length === 0) {
      throw new Error(`AgentBus startup handshake is missing ${key}.`);
    }
  }
  const compatibility = assessDaemonCompatibility(
    String(value.agentbus_version),
    String(value.protocol_version)
  );
  if (!compatibility.compatible) {
    throw new Error(compatibility.message);
  }
  if (
    value.token_delivery !== "parent_process_stdout" ||
    typeof value.port !== "number" ||
    !Number.isInteger(value.port) ||
    value.port < 1 ||
    value.port > 65_535 ||
    typeof value.pid !== "number" ||
    !Number.isInteger(value.pid) ||
    value.pid < 1 ||
    String(value.bearer_token).length < 32
  ) {
    throw new Error("AgentBus startup handshake is incompatible or invalid.");
  }
  return value as unknown as ReadyHandshake;
}

export function parseRegistry(content: string): RegistryDocument {
  let value: unknown;
  try {
    value = JSON.parse(content);
  } catch {
    throw new Error("AgentBus daemon registry is not valid JSON.");
  }
  if (!isRecord(value) || value.version !== 1 || !Array.isArray(value.daemons)) {
    throw new Error("AgentBus daemon registry has an unsupported format.");
  }
  const serialized = JSON.stringify(value).toLowerCase();
  if (serialized.includes("bearer_token") || serialized.includes('"token"')) {
    throw new Error("AgentBus daemon registry unexpectedly contains secret fields.");
  }
  return value as unknown as RegistryDocument;
}

export function buildLaunchSpec(settings: LaunchSettings): LaunchSpec {
  const registryPath = settings.registryPath
    ? resolve(settings.registryPath)
    : join(homedir(), ".agentbus", "daemons.json");
  const common = [
    "serve",
    ...(settings.configPath
      ? ["--config", resolve(settings.configPath)]
      : []),
    "--host",
    "127.0.0.1",
    "--port",
    "0",
    "--json-ready",
    "--foreground",
    "--registry-path",
    registryPath,
    "--log-level",
    settings.logLevel ?? "warning"
  ];
  if (settings.executablePath) {
    return {
      command: resolve(settings.executablePath),
      args: common,
      registryPath
    };
  }
  if (settings.pythonPath) {
    return {
      command: resolve(settings.pythonPath),
      args: ["-m", "agentbus.cli", ...common],
      registryPath
    };
  }
  return { command: "agentbus", args: common, registryPath };
}

export function buildStopSpec(
  settings: LaunchSettings,
  daemonId: string
): LaunchSpec {
  const launch = buildLaunchSpec(settings);
  const commandArgs = [
    "daemon",
    "--registry-path",
    launch.registryPath,
    "stop",
    daemonId
  ];
  if (settings.executablePath) {
    return { ...launch, args: commandArgs };
  }
  if (settings.pythonPath) {
    return { ...launch, args: ["-m", "agentbus.cli", ...commandArgs] };
  }
  return { ...launch, args: commandArgs };
}

export function daemonBaseUrl(entry: {
  host: string;
  port: number;
}): string {
  const host = entry.host === "::1" ? "[::1]" : entry.host;
  return `http://${host}:${entry.port}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
