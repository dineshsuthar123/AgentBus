import { spawn } from "node:child_process";
import { resolve } from "node:path";

const MAX_ARGUMENTS = 64;
const MAX_ARGUMENT_LENGTH = 4_096;
const MAX_TOTAL_ARGUMENT_LENGTH = 32_768;
const DEFAULT_OUTPUT_LIMIT = 256 * 1_024;
const DEFAULT_TIMEOUT_MS = 120_000;

export interface ProductLaunchSettings {
  executablePath?: string;
  pythonPath?: string;
}

export interface ProductCommandSpec {
  command: string;
  args: string[];
}

export interface ProductCommandResult {
  exitCode: number;
  stdout: string;
  stderr: string;
}

export interface ProductCommandOptions {
  cwd?: string;
  timeoutMs?: number;
  maxOutputBytes?: number;
}

export function buildProductCommandSpec(
  settings: ProductLaunchSettings,
  args: readonly string[]
): ProductCommandSpec {
  validateArguments(args);
  if (settings.executablePath) {
    return { command: resolve(settings.executablePath), args: [...args] };
  }
  if (settings.pythonPath) {
    return {
      command: resolve(settings.pythonPath),
      args: ["-m", "agentbus.cli", ...args]
    };
  }
  return { command: "agentbus", args: [...args] };
}

export function runProductCommand(
  spec: ProductCommandSpec,
  options: ProductCommandOptions = {}
): Promise<ProductCommandResult> {
  validateCommand(spec.command);
  validateArguments(spec.args);
  const timeoutMs = boundedPositive(
    options.timeoutMs,
    DEFAULT_TIMEOUT_MS,
    1_000,
    10 * 60_000
  );
  const outputLimit = boundedPositive(
    options.maxOutputBytes,
    DEFAULT_OUTPUT_LIMIT,
    1_024,
    4 * 1_024 * 1_024
  );
  return new Promise((resolveResult, reject) => {
    const child = spawn(spec.command, spec.args, {
      cwd: options.cwd,
      env: process.env,
      shell: false,
      windowsHide: true
    });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let outputBytes = 0;
    let settled = false;
    const finishError = (error: Error): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.kill();
      reject(error);
    };
    const append = (target: Buffer[], chunk: Buffer): void => {
      outputBytes += chunk.length;
      if (outputBytes > outputLimit) {
        finishError(new Error("AgentBus command output exceeded its safety limit."));
        return;
      }
      target.push(chunk);
    };
    const timer = setTimeout(
      () => finishError(new Error("AgentBus command exceeded its time limit.")),
      timeoutMs
    );
    child.stdout.on("data", (chunk: Buffer) => append(stdout, chunk));
    child.stderr.on("data", (chunk: Buffer) => append(stderr, chunk));
    child.once("error", finishError);
    child.once("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolveResult({
        exitCode: code ?? 1,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8")
      });
    });
  });
}

function validateCommand(command: string): void {
  if (
    typeof command !== "string" ||
    command.length === 0 ||
    command.length > MAX_ARGUMENT_LENGTH ||
    command.includes("\0")
  ) {
    throw new Error("AgentBus command executable is invalid.");
  }
}

function validateArguments(args: readonly string[]): void {
  if (args.length === 0 || args.length > MAX_ARGUMENTS) {
    throw new Error("AgentBus command argument count is outside its safety limit.");
  }
  let total = 0;
  for (const value of args) {
    if (
      typeof value !== "string" ||
      value.length === 0 ||
      value.length > MAX_ARGUMENT_LENGTH ||
      value.includes("\0")
    ) {
      throw new Error("AgentBus command contains an invalid argument.");
    }
    total += value.length;
  }
  if (total > MAX_TOTAL_ARGUMENT_LENGTH) {
    throw new Error("AgentBus command arguments exceeded their safety limit.");
  }
}

function boundedPositive(
  value: number | undefined,
  fallback: number,
  minimum: number,
  maximum: number
): number {
  if (value === undefined) return fallback;
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error("AgentBus command limit is outside the supported range.");
  }
  return value;
}
