import { copyFile, realpath, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import path from "node:path";

const studio = path.resolve(import.meta.dirname, "..");
const output = path.join(studio, "showcase-dist");
if (path.relative(output, await realpath(output))) throw new Error("Showcase output cannot be redirected");
for (const [packagePath, name] of [
  ["@fontsource-variable/ibm-plex-sans", "LICENSE-ibm-plex-sans.txt"],
  ["@fontsource/ibm-plex-mono", "LICENSE-ibm-plex-mono.txt"],
]) await copyFile(path.join(studio, "node_modules", packagePath, "LICENSE"), path.join(output, name));
const options = { cwd: studio, encoding: "utf8", windowsHide: true, stdio: ["ignore", "pipe", "pipe"] };
const commit = execFileSync("git", ["rev-parse", "HEAD"], options).trim();
let dirty = false;
try { execFileSync("git", ["diff", "--quiet", "HEAD", "--"], options); }
catch (error) { if (error.status !== 1) throw error; dirty = true; }
await writeFile(path.join(output, ".nojekyll"), "");
await writeFile(path.join(output, "build.json"), JSON.stringify({ kind: "public-showcase-preview", source_commit: commit, tracked_source_dirty: dirty, control_plane_included: false, backend_hosted: false }, null, 2) + "\n");
