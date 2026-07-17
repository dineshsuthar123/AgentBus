import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const extensionRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(extensionRoot, "..", "..");
const candidates = [
  process.env.AGENTBUS_PYTHON,
  resolve(repositoryRoot, ".venv", "Scripts", "python.exe"),
  resolve(repositoryRoot, ".venv", "bin", "python"),
  process.platform === "win32" ? "python" : "python3"
].filter(Boolean);
const python = candidates.find((candidate) =>
  candidate.includes("/") || candidate.includes("\\") ? existsSync(candidate) : true
);
if (!python) {
  throw new Error("Unable to locate Python for AgentBus protocol freshness check.");
}
const result = spawnSync(
  python,
  ["-m", "agentbus.cli", "control-schema", "export", "--check"],
  {
    cwd: repositoryRoot,
    encoding: "utf8",
    shell: false,
    stdio: "inherit"
  }
);
if (result.error) {
  throw result.error;
}
process.exitCode = result.status ?? 1;
