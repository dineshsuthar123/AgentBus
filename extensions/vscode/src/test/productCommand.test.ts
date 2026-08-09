import assert from "node:assert/strict";
import { resolve } from "node:path";
import test from "node:test";
import {
  buildProductCommandSpec,
  runProductCommand
} from "../productCommand";

test("product commands prefer explicit executable then Python without a shell", () => {
  const executable = buildProductCommandSpec(
    { executablePath: "bin/agentbus", pythonPath: "bin/python" },
    ["version", "--json"]
  );
  assert.deepEqual(executable, {
    command: resolve("bin/agentbus"),
    args: ["version", "--json"]
  });

  const python = buildProductCommandSpec(
    { pythonPath: "bin/python" },
    ["doctor", "--json"]
  );
  assert.deepEqual(python, {
    command: resolve("bin/python"),
    args: ["-m", "agentbus.cli", "doctor", "--json"]
  });
  assert.deepEqual(buildProductCommandSpec({}, ["quickstart", "--json"]), {
    command: "agentbus",
    args: ["quickstart", "--json"]
  });
});

test("product command arguments reject injection and unbounded input", () => {
  assert.throws(
    () => buildProductCommandSpec({}, ["doctor\0--live-provider"]),
    /invalid argument/i
  );
  assert.throws(
    () => buildProductCommandSpec({}, Array.from({ length: 65 }, () => "arg")),
    /argument count/i
  );
  assert.throws(
    () => runProductCommand({ command: "node\0unsafe", args: ["--version"] }),
    /executable is invalid/i
  );
});

test("product command runner captures bounded local output", async () => {
  const result = await runProductCommand(
    {
      command: process.execPath,
      args: ["-e", "process.stdout.write('offline-ok')"]
    },
    { timeoutMs: 5_000, maxOutputBytes: 2_048 }
  );
  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout, "offline-ok");
  assert.equal(result.stderr, "");
});
