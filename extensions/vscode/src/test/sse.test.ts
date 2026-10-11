import assert from "node:assert/strict";
import test from "node:test";
import { SyndraClient } from "../apiClient";
import { ReconnectingSseClient, SseParser } from "../sse";

const token = "sse-test-token-with-more-than-thirty-two-bytes";

test("SSE handles every CRLF split and preserves data whitespace", () => {
  const source = ": heartbeat\r\nid: 1\r\ndata:  one\r\ndata: two\r\n\r\n";
  const expected = [{ id: "1", event: "message", data: " one\ntwo" }];
  for (let split = 0; split <= source.length; split += 1) {
    const parser = new SseParser();
    assert.deepEqual([...parser.push(source.slice(0, split)), ...parser.push(source.slice(split))], expected);
  }
  const parser = new SseParser();
  assert.deepEqual(Array.from(source).flatMap((character) => parser.push(character)), expected);
});

test("SSE rejects oversized unfinished frames", () => {
  assert.throws(() => new SseParser().push("data: " + "x".repeat(1_048_576)), /limit/i);
});

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
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({
    cancel() { cancelled = true; }
  });
  const client = new SyndraClient("http://127.0.0.1:43123", token);
  const stream = new ReconnectingSseClient(client, () => undefined, {
    fetcher: async () => new Response(body, { status: 200 })
  });

  stream.start();
  for (let attempt = 0; attempt < 20 && !stream.isConnected; attempt += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }

  assert.equal(stream.isConnected, true);
  stream.stop();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(cancelled, true);
  assert.equal(stream.isConnected, false);
});

test("SSE authentication failure stops without retrying", async () => {
  let calls = 0;
  let expired = false;
  const stream = new ReconnectingSseClient(new SyndraClient("http://127.0.0.1:43123", token), () => undefined, {
    fetcher: async () => { calls += 1; return new Response(null, { status: 403 }); },
    onAuthenticationFailure: () => { expired = true; }
  });
  stream.start();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(expired, true);
  assert.equal(calls, 1);
  assert.equal(stream.isConnected, false);
});

test("SSE stops empty-stream reconnects at its retry budget", async () => {
  let calls = 0;
  let exhausted = false;
  const stream = new ReconnectingSseClient(new SyndraClient("http://127.0.0.1:43123", token), () => undefined, {
    maximumReconnectAttempts: 2, reconnectDelayMs: 0,
    fetcher: async () => { calls += 1; return new Response(""); },
    onRetryExhausted: () => { exhausted = true; }
  });
  stream.start();
  for (let i = 0; i < 100 && !exhausted; i += 1) await new Promise((resolve) => setImmediate(resolve));
  stream.stop();
  assert.equal(calls, 2);
  assert.equal(exhausted, true);
});
