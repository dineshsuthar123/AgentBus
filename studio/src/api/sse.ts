import type { EventEnvelope } from "./types";
import { StudioClient } from "./client";

export class SseParser {
  private buffer = "";

  public push(chunk: string): Array<{ id?: string; data: string }> {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const messages: Array<{ id?: string; data: string }> = [];
    let boundary = this.buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      let id: string | undefined;
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("id:")) id = line.slice(3).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) messages.push({ id, data: data.join("\n") });
      boundary = this.buffer.indexOf("\n\n");
    }
    return messages;
  }
}

export interface EventStreamOptions {
  onEvent: (event: EventEnvelope) => void;
  onState: (connected: boolean) => void;
  fetcher?: typeof fetch;
  reconnectDelayMs?: number;
}

export class StudioEventStream {
  private controller?: AbortController;
  private active = false;
  private cursor = 0;

  public constructor(
    private readonly client: StudioClient,
    private readonly options: EventStreamOptions
  ) {}

  public start(runId?: string): void {
    if (this.active) return;
    this.active = true;
    void this.connect(runId);
  }

  public stop(): void {
    this.active = false;
    this.controller?.abort();
    this.options.onState(false);
  }

  private async connect(runId?: string): Promise<void> {
    const fetcher = this.options.fetcher ?? fetch;
    while (this.active) {
      this.controller = new AbortController();
      try {
        const response = await fetcher(this.client.eventsUrl(runId, this.cursor), {
          headers: {
            Accept: "text/event-stream",
            Authorization: this.client.authorizationHeader(),
            ...(this.cursor ? { "Last-Event-ID": String(this.cursor) } : {})
          },
          signal: this.controller.signal
        });
        if (!response.ok || !response.body) throw new Error("SSE unavailable");
        this.options.onState(true);
        await this.consume(response.body);
      } catch {
        this.options.onState(false);
        if (!this.active || this.controller.signal.aborted) return;
        await new Promise((resolve) => window.setTimeout(resolve, this.options.reconnectDelayMs ?? 1_000));
      }
    }
  }

  private async consume(stream: ReadableStream<Uint8Array>): Promise<void> {
    const parser = new SseParser();
    const decoder = new TextDecoder();
    const reader = stream.getReader();
    while (this.active) {
      const { done, value } = await reader.read();
      if (done) return;
      for (const message of parser.push(decoder.decode(value, { stream: true }))) {
        const event = JSON.parse(message.data) as EventEnvelope;
        if (!Number.isInteger(event.sequence) || event.sequence <= this.cursor) continue;
        this.cursor = event.sequence;
        this.options.onEvent(event);
      }
    }
  }
}
