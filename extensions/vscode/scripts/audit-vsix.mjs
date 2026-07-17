import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import JSZip from "jszip";

const input = resolve(process.argv[2] ?? "agentbus-vscode.vsix");
const archive = await JSZip.loadAsync(await readFile(input));
const names = Object.keys(archive.files).sort();
const forbidden = [
  /(^|\/)node_modules\//i,
  /(^|\/)\.env(?:\.|$)/i,
  /\.(?:db|sqlite|sqlite3|pyc)$/i,
  /(^|\/)\.vscode-test\//i,
  /(^|\/)(?:runs|worktrees|evaluation-output)\//i,
  /(^|\/)__pycache__\//i,
  /(^|\/)agentbus-vscode\.vsix$/i
];
const violations = names.filter((name) =>
  forbidden.some((pattern) => pattern.test(name))
);
const secretPatterns = [
  /AZURE_OPENAI_API_KEY\s*=/i,
  /Bearer\s+[A-Za-z0-9._~+/=-]{20,}/i,
  /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/
];
for (const [name, item] of Object.entries(archive.files)) {
  if (item.dir || item._data?.uncompressedSize > 1_000_000) {
    continue;
  }
  const content = await item.async("string");
  if (secretPatterns.some((pattern) => pattern.test(content))) {
    violations.push(`${name} (secret-like content)`);
  }
}
if (violations.length > 0) {
  throw new Error(`VSIX audit failed:\n${violations.join("\n")}`);
}
process.stdout.write(`VSIX audit passed: ${names.length} entries\n`);
