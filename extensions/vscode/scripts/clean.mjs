import { rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
for (const relative of ["out", "agentbus-vscode.vsix"]) {
  const target = resolve(root, relative);
  if (!target.startsWith(`${root}\\`) && !target.startsWith(`${root}/`)) {
    throw new Error(`Refusing to remove path outside extension root: ${target}`);
  }
  await rm(target, { force: true, recursive: true });
}
