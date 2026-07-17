import assert from "node:assert/strict";
import test from "node:test";
import { SseParser } from "../sse";

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
