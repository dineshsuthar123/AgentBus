import { homedir } from "node:os";
import { readFile } from "node:fs/promises";
import { basename, resolve } from "node:path";
import JSZip from "jszip";

const input = resolve(process.argv[2] ?? "agentbus-vscode.vsix");
const archive = await JSZip.loadAsync(await readFile(input));
const names = Object.keys(archive.files).sort();
const required = [
  "[Content_Types].xml",
  "extension.vsixmanifest",
  "extension/LICENSE.txt",
  "extension/readme.md",
  "extension/media/agentbus.svg",
  "extension/out/extension.js",
  "extension/package.json"
];
const forbidden = [
  /(^|\/)node_modules\//i,
  /(^|\/)\.env(?:\.|$)/i,
  /\.(?:db|log|pem|pfx|p12|sqlite|sqlite3|pyc|pyo|ts|map)$/i,
  /(^|\/)\.vscode-test\//i,
  /(^|\/)(?:\.agentbus|\.venv|build|dist|runs|traces|worktrees|evaluation-output)\//i,
  /(^|\/)__pycache__\//i,
  /(^|\/)src\//i,
  /(^|\/)test(?:s)?\//i,
  /(^|\/).*profile.*\//i,
  /(^|\/)agentbus-support-.*\.zip$/i,
  /(^|\/)agentbus-vscode\.vsix$/i
];
const violations = [];
let expandedBytes = 0;
if (names.length > 2_000) {
  violations.push(`archive entry limit exceeded: ${names.length}`);
}
for (const name of names) {
  const item = archive.files[name];
  const normalized = name.replaceAll("\\", "/");
  if (
    normalized.startsWith("/") ||
    normalized.split("/").includes("..") ||
    normalized.includes("\0")
  ) {
    violations.push(`${name} (unsafe archive path)`);
  }
  if (forbidden.some((pattern) => pattern.test(normalized))) {
    violations.push(`${name} (forbidden path)`);
  }
  if (!isAllowed(normalized, item.dir)) {
    violations.push(`${name} (unexpected VSIX content)`);
  }
  const mode = (item.unixPermissions ?? 0) & 0o170000;
  if (mode === 0o120000) {
    violations.push(`${name} (symbolic link)`);
  }
  const size = item._data?.uncompressedSize ?? 0;
  expandedBytes += size;
  if (size > 5_000_000) {
    violations.push(`${name} (file exceeds 5 MB)`);
  }
}
if (expandedBytes > 25_000_000) {
  violations.push(`expanded archive exceeds 25 MB: ${expandedBytes}`);
}
for (const name of required) {
  if (!archive.files[name] || archive.files[name].dir) {
    violations.push(`${name} (required file missing)`);
  }
}
const secretPatterns = [
  /\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b/,
  /\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,}\b/,
  /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/,
  /AZURE_OPENAI_API_KEY\s*=\s*[^\s"']{20,}/i,
  /Bearer\s+[A-Za-z0-9._~+/=-]{32,}/i
];
for (const [name, item] of Object.entries(archive.files)) {
  if (item.dir || item._data?.uncompressedSize > 5_000_000) {
    continue;
  }
  const content = await item.async("string");
  if (secretPatterns.some((pattern) => pattern.test(content))) {
    violations.push(`${name} (secret-like content)`);
  }
  if (containsPrivateKey(content)) {
    violations.push(`${name} (private-key block)`);
  }
  const home = homedir();
  if (home.length > 3 && content.toLowerCase().includes(home.toLowerCase())) {
    violations.push(`${name} (personal absolute path)`);
  }
}

const packageItem = archive.files["extension/package.json"];
if (packageItem && !packageItem.dir) {
  const packageMetadata = JSON.parse(await packageItem.async("string"));
  if (packageMetadata.name !== "agentbus-vscode") {
    violations.push("extension/package.json (unexpected package name)");
  }
  if (packageMetadata.main !== "./out/extension.js") {
    violations.push("extension/package.json (unsafe extension entry point)");
  }
  if (packageMetadata.private !== true) {
    violations.push("extension/package.json (npm publication guard missing)");
  }
  const compatibility = packageMetadata.agentbusCompatibility ?? {};
  if (
    compatibility.python !== ">=0.6.0b1,<0.7.0" ||
    compatibility.controlProtocol !== "1.0" ||
    compatibility.stateSchema !== 6
  ) {
    violations.push("extension/package.json (AgentBus compatibility metadata mismatch)");
  }
  const manifest = await archive.files["extension.vsixmanifest"]?.async("string");
  if (manifest && !manifest.includes(`Version="${packageMetadata.version}"`)) {
    violations.push("extension.vsixmanifest (version mismatch)");
  }
}
if (violations.length > 0) {
  throw new Error(`VSIX audit failed:\n${[...new Set(violations)].sort().join("\n")}`);
}
process.stdout.write(
  `VSIX audit passed: ${basename(input)}, ${names.length} entries, ${expandedBytes} expanded bytes\n`
);

function isAllowed(name, directory) {
  if (name === "[Content_Types].xml" || name === "extension.vsixmanifest") {
    return !directory;
  }
  if (directory) {
    return ["extension/", "extension/media/", "extension/out/"].includes(name);
  }
  return (
    [
      "extension/LICENSE.txt",
      "extension/readme.md",
      "extension/package.json",
      "extension/media/agentbus.svg"
    ].includes(name) || /^extension\/out\/[A-Za-z0-9._/-]+\.js$/u.test(name)
  );
}

function containsPrivateKey(content) {
  for (const prefix of ["", "RSA ", "EC ", "OPENSSH "]) {
    if (
      content.includes(`-----BEGIN ${prefix}PRIVATE KEY-----`) &&
      content.includes(`-----END ${prefix}PRIVATE KEY-----`)
    ) {
      return true;
    }
  }
  return false;
}
