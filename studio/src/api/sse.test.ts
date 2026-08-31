import { describe, expect, it, vi } from "vitest";
import type { StudioClient } from "./client";
import { SseParser, StudioEventStream, type StreamStatus } from "./sse";

describe("SseParser", () => {
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
