import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import JSZip from "jszip";

const auditScript = resolve("scripts/audit-vsix.mjs");

test("VSIX audit accepts bounded runtime contents and rejects source", async () => {
  const temporary = await mkdtemp(join(tmpdir(), "agentbus-vsix-audit-"));
  try {
    const safe = join(temporary, "safe.vsix");
    await writeFile(safe, await fixture().generateAsync({ type: "nodebuffer" }));

    const accepted = spawnSync(process.execPath, [auditScript, safe], {
      encoding: "utf8"
    });
    assert.equal(accepted.status, 0, accepted.stderr);
    assert.match(accepted.stdout, /VSIX audit passed/u);

    const unsafe = fixture();
    unsafe.file("extension/src/leak.ts", "export const leaked = true;\n");
    const unsafePath = join(temporary, "unsafe.vsix");
    await writeFile(
      unsafePath,
      await unsafe.generateAsync({ type: "nodebuffer" })
    );

    const rejected = spawnSync(process.execPath, [auditScript, unsafePath], {
      encoding: "utf8"
    });
    assert.notEqual(rejected.status, 0);
    assert.match(rejected.stderr, /forbidden path|unexpected VSIX content/u);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

function fixture() {
  const archive = new JSZip();
  archive.file("[Content_Types].xml", "<Types />\n");
  archive.file(
    "extension.vsixmanifest",
    '<PackageManifest><Identity Version="0.6.0-beta.1" /></PackageManifest>\n'
  );
  archive.file("extension/LICENSE.txt", "MIT\n");
  archive.file("extension/readme.md", "# AgentBus\n");
  archive.file("extension/media/agentbus.svg", "<svg />\n");
  archive.file("extension/out/extension.js", '"use strict";\n');
  archive.file(
    "extension/package.json",
    JSON.stringify({
      name: "agentbus-vscode",
      version: "0.6.0-beta.1",
      private: true,
      main: "./out/extension.js",
      agentbusCompatibility: {
        python: ">=0.6.0b1,<0.7.0",
        controlProtocol: "1.0",
        stateSchema: 6
      }
    })
  );
  return archive;
}
