import assert from "node:assert/strict";
import test from "node:test";
import {
  buildLaunchSpec,
  buildStopSpec,
  daemonBaseUrl,
  parseReadyHandshake,
  parseRegistry
} from "../daemonProtocol";

const token = "a-secure-test-token-with-more-than-thirty-two-bytes";

test("startup handshake validates protocol and delivery mode", () => {
  const handshake = parseReadyHandshake(
    JSON.stringify({
      protocol_version: "1.0",
      host: "127.0.0.1",
      port: 43123,
      daemon_id: "daemon-1",
      pid: 42,
      agentbus_version: "0.2",
      registry_path: "registry.json",
      bearer_token: token,
      token_delivery: "parent_process_stdout"
    })
  );

  assert.equal(handshake.bearer_token, token);
  assert.equal(daemonBaseUrl(handshake), "http://127.0.0.1:43123");
});

test("startup handshake rejects downgrade and malformed token", () => {
  assert.throws(
    () =>
      parseReadyHandshake(
        JSON.stringify({
          protocol_version: "0.9",
          host: "127.0.0.1",
          port: 43123,
          daemon_id: "daemon-1",
          pid: 42,
          agentbus_version: "0.2",
          registry_path: "registry.json",
          bearer_token: "short",
          token_delivery: "parent_process_stdout"
        })
      ),
    /incompatible/
  );
});

test("registry parser rejects secret fields", () => {
  assert.throws(
    () => parseRegistry(JSON.stringify({ version: 1, daemons: [], token })),
    /secret fields/
  );
});

test("launch and stop specs never put bearer tokens in arguments", () => {
  const settings = {
    pythonPath: "C:/Python/python.exe",
    registryPath: "C:/safe/daemons.json",
    logLevel: "error" as const
  };
  const launch = buildLaunchSpec(settings);
  const stop = buildStopSpec(settings, "daemon-1");

  assert.deepEqual(launch.args.slice(0, 3), ["-m", "agentbus.cli", "serve"]);
  assert.ok(stop.args.includes("daemon-1"));
  assert.equal(JSON.stringify({ launch, stop }).includes(token), false);
});
