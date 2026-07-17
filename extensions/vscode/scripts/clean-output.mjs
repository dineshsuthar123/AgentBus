import { rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = resolve(root, "out");
if (!output.startsWith(`${root}\\`) && !output.startsWith(`${root}/`)) {
  throw new Error(`Refusing to remove path outside extension root: ${output}`);
}
await rm(output, { force: true, recursive: true });
