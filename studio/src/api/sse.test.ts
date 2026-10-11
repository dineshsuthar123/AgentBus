import { describe, expect, it, vi } from "vitest";
import type { StudioClient } from "./client";
import { SseParser, StudioEventStream, type StreamStatus } from "./sse";

describe("SseParser", () => {
  it("handles every split of CRLF frames without losing data whitespace", () => {
    const source = ": heartbeat\r\nid: 1\r\ndata:  one\r\ndata: two\r\n\r\n";
    for (let split = 0; split <= source.length; split += 1) {
      const parser = new SseParser();
      expect([...parser.push(source.slice(0, split)), ...parser.push(source.slice(split))]).toEqual([
        { id: "1", data: " one\ntwo" }
      ]);
    }
    const parser = new SseParser();
    expect(Array.from(source).flatMap((character) => parser.push(character))).toEqual([
      { id: "1", data: " one\ntwo" }
    ]);
  });

  it("rejects oversized unfinished frames", () => {
    expect(() => new SseParser().push("data: " + "x".repeat(1_048_576))).toThrow(/limit/i);
  });

  it("waits for complete frames and combines multiline data", () => {
    const parser = new SseParser();
    expect(parser.push("id: 4\r\ndata: {\"sequence\":4,")).toEqual([]);
    expect(parser.push("\r\ndata: \"event_type\":\"run.updated\"}\r\n\r\n")).toEqual([
      { id: "4", data: "{\"sequence\":4,\n\"event_type\":\"run.updated\"}" }
    ]);
  });

  it("emits multiple frames in order", () => {
    const parser = new SseParser();
    expect(parser.push("id: 1\ndata: one\n\nid: 2\ndata: two\n\n")).toEqual([
      { id: "1", data: "one" },
      { id: "2", data: "two" }
    ]);
  });

  it("stops after authentication failure without automatic retries", async () => {
    const expired = vi.fn();
    const fetcher = vi.fn<typeof fetch>(async () => new Response(null, { status: 403 }));
    const client = { authorizationHeader: () => "", eventsUrl: () => new URL("http://127.0.0.1:8765/api/v1/events") } as StudioClient;
    const stream = new StudioEventStream(client, { fetcher, onAuthenticationFailure: expired, onState: () => undefined, onEvent: () => undefined });
    stream.start();
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce());
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0][1]?.headers).not.toHaveProperty("Authorization");
    expect(stream.currentStatus.phase).toBe("stopped");
  });

  it("bounds repeated network failures", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => { throw new TypeError("offline"); });
    const client = { authorizationHeader: () => "", eventsUrl: () => new URL("http://127.0.0.1:8765/api/v1/events") } as StudioClient;
    const stream = new StudioEventStream(client, { fetcher, maximumReconnectAttempts: 2, reconnectDelayMs: 10, jitterRatio: 0, onState: () => undefined, onEvent: () => undefined });
    stream.start();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(stream.currentStatus.phase).toBe("degraded");
    stream.stop();
  });

  it("decodes UTF-8 split into individual bytes and reconciles sequence gaps", async () => {
    const reconcile = vi.fn();
    const observed: string[] = [];
    const bytes = new TextEncoder().encode(sseFrame(1) + sseFrame(3).replace("run.updated", "run.caf\u00e9"));
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
      controller.close();
    } });
    const client = { authorizationHeader: () => "", eventsUrl: () => new URL("http://127.0.0.1:8765/api/v1/events") } as StudioClient;
    const stream = new StudioEventStream(client, { fetcher: async () => new Response(body), onState: () => undefined, onReconcile: reconcile,
      onEvent: (event) => { observed.push(event.event_type); if (event.sequence === 3) stream.stop(); } });
    stream.start();
    await vi.waitFor(() => expect(observed).toEqual(["run.updated", "run.caf\u00e9"]));
    expect(reconcile).toHaveBeenCalledWith(1);
  });

  it("resumes from the monotonic cursor, drops duplicates, and reconciles after reconnect", async () => {
    const statuses: StreamStatus[] = [];
    const observed: number[] = [];
    const drops: string[] = [];
    const reconciled: number[] = [];
    const frames = [
      sseFrame(1),
      `${sseFrame(1)}${sseFrame(2)}`
    ];
    const fetcher = vi.fn<typeof fetch>(async () => new Response(frames.shift(), {
      status: 200,
      headers: { "Content-Type": "text/event-stream" }
    }));
    const client = {
      authorizationHeader: () => "Bearer redacted",
      eventsUrl: (_runId?: string, cursor = 0) => new URL(`http://127.0.0.1:8765/api/v1/events?after=${cursor}`)
    } as StudioClient;
    const stream = new StudioEventStream(client, {
      fetcher,
      jitterRatio: 0,
      reconnectDelayMs: 10,
      onDrop: (reason) => drops.push(reason),
      onEvent: (event) => {
        observed.push(event.sequence);
        if (event.sequence === 2) stream.stop();
      },
      onReconcile: (cursor) => { reconciled.push(cursor); },
      onState: () => undefined,
      onStatus: (status) => statuses.push(status)
    });

    stream.start("run-1");
    await vi.waitFor(() => expect(observed).toEqual([1, 2]));

    expect(fetcher).toHaveBeenCalledTimes(2);
    const [, secondInit] = fetcher.mock.calls[1];
    expect(secondInit?.headers).toMatchObject({ "Last-Event-ID": "1" });
    expect(drops).toEqual(["duplicate"]);
    expect(reconciled).toEqual([1]);
    expect(statuses.map((status) => status.phase)).toEqual(expect.arrayContaining([
      "connecting",
      "connected",
      "degraded",
      "reconnecting",
      "restored",
      "stopped"
    ]));
  });
});

function sseFrame(sequence: number): string {
  return `id: ${sequence}\ndata: ${JSON.stringify({
    sequence,
    event_type: "run.updated",
    timestamp: "2026-08-31T10:00:00Z",
    run_id: "run-1",
    payload: { status: "running" }
  })}\n\n`;
}
