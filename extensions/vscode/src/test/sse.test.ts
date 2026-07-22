import assert from "node:assert/strict";
import test from "node:test";
import { AgentBusClient } from "../apiClient";
import { ReconnectingSseClient, SseParser } from "../sse";

const token = "sse-test-token-with-more-than-thirty-two-bytes";

test("SSE parser handles chunk boundaries and multiline data", () => {
  const parser = new SseParser();

  assert.deepEqual(parser.push("id: 4\nevent: run_"), []);
  const events = parser.push("started\ndata: {\"sequence\":4,\ndata: \"ok\":true}\n\n");

  assert.deepEqual(events, [
    {
      id: "4",
      event: "run_started",
      data: "{\"sequence\":4,\n\"ok\":true}"
    }
  ]);
});

test("SSE parser ignores heartbeat comments", () => {
  const parser = new SseParser();

  assert.deepEqual(parser.push(": heartbeat\n\n"), []);
});

test("SSE client exposes connection readiness and clears it on stop", async () => {
  let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      streamController = controller;
    }
  });
  const client = new AgentBusClient("http://127.0.0.1:43123", token);
  const stream = new ReconnectingSseClient(client, () => undefined, {
    fetcher: async () => new Response(body, { status: 200 })
  });

  stream.start();
  for (let attempt = 0; attempt < 20 && !stream.isConnected; attempt += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }

  assert.equal(stream.isConnected, true);
  stream.stop();
  streamController?.close();
  assert.equal(stream.isConnected, false);
});
