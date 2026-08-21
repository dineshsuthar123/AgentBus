import assert from "node:assert/strict";
import { resolve } from "node:path";
import test from "node:test";

import {
  freshProfileSessionName,
  freshProfileSessionPrefix,
  freshProfileStagingRoot
} from "./runFreshProfile";

test("Windows fresh-profile sessions stay within the IPC path budget", () => {
  const name = freshProfileSessionName(0x7fff_ffff, "win32");

  assert.match(name, /^agentbus-vfp-[0-9a-z]+$/u);
  assert.ok(name.length <= 20);
  assert.equal(freshProfileSessionPrefix("win32"), "agentbus-vfp-");
  assert.equal(
    freshProfileStagingRoot(
      "win32",
      "C:\\Users\\runneradmin\\AppData\\Local\\Temp"
    ),
    "C:\\tmp"
  );
});

test("non-Windows fresh-profile sessions retain descriptive ownership", () => {
  assert.equal(
    freshProfileSessionName(12345, "linux"),
    "agentbus-vscode-fresh-profile-12345"
  );
  assert.equal(
    freshProfileSessionPrefix("darwin"),
    "agentbus-vscode-fresh-profile-"
  );
  assert.equal(freshProfileStagingRoot("linux", "/var/tmp"), resolve("/var/tmp"));
});

test("fresh-profile session names reject invalid process IDs", () => {
  for (const pid of [0, -1, 1.5, Number.NaN, 0x8000_0000]) {
    assert.throws(
      () => freshProfileSessionName(pid, "win32"),
      /process ID is outside the supported range/u
    );
  }
});

test("fresh-profile Windows staging rejects non-local roots", () => {
  assert.throws(
    () => freshProfileStagingRoot("win32", "\\\\server\\share\\tmp"),
    /requires a local Windows drive/u
  );
});
