import { describe, expect, it } from "vitest";
import { SseParser } from "./sse";

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
});
