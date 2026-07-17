import type { EventEnvelope } from "./generated/protocol";
import type { AgentBusClient } from "./apiClient";

export class SseParser {
  private buffer = "";
  private eventType = "message";
  private eventId: string | undefined;
  private data: string[] = [];

  public push(chunk: string): Array<{
    id?: string;
    event: string;
    data: string;
  }> {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const output: Array<{ id?: string; event: string; data: string }> = [];
    let boundary = this.buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) {
          continue;
        }
        const separator = line.indexOf(":");
        const field = separator < 0 ? line : line.slice(0, separator);
        const value =
          separator < 0
            ? ""
            : line.slice(separator + 1).replace(/^ /, "");
        if (field === "id") {
          this.eventId = value;
        } else if (field === "event") {
          this.eventType = value;
        } else if (field === "data") {
          this.data.push(value);
        }
      }
      if (this.data.length > 0) {
        output.push({
          id: this.eventId,
          event: this.eventType,
          data: this.data.join("\n")
        });
      }
      this.eventType = "message";
      this.data = [];
      boundary = this.buffer.indexOf("\n\n");
    }
    return output;
  }
}

export interface SseClientOptions {
  reconnectDelayMs?: number;
  onReconnectFailure?: () => Promise<void>;
  fetcher?: typeof fetch;
}

export class ReconnectingSseClient {
  private controller: AbortController | undefined;
  private running = false;
  private lastSequence = 0;

  public constructor(
    private readonly client: AgentBusClient,
    private readonly onEvent: (event: EventEnvelope) => void,
    private readonly options: SseClientOptions = {}
  ) {}

  public start(runId?: string): void {
    if (this.running) {
      return;
    }
    this.running = true;
    void this.run(runId);
  }

  public stop(): void {
    this.running = false;
    this.controller?.abort();
  }

  public get cursor(): number {
    return this.lastSequence;
  }

  private async run(runId?: string): Promise<void> {
    const fetcher = this.options.fetcher ?? fetch;
    while (this.running) {
      this.controller = new AbortController();
      try {
        const response = await fetcher(this.client.eventsUrl(runId), {
          headers: {
            Accept: "text/event-stream",
            Authorization: this.client.authorizationHeader(),
            ...(this.lastSequence > 0
              ? { "Last-Event-ID": String(this.lastSequence) }
              : {})
          },
          signal: this.controller.signal
        });
        if (!response.ok || !response.body) {
          throw new Error(`SSE connection failed with HTTP ${response.status}.`);
        }
        await this.consume(response.body);
      } catch {
        if (!this.running || this.controller.signal.aborted) {
          return;
        }
        await this.options.onReconnectFailure?.();
        await delay(this.options.reconnectDelayMs ?? 1_000);
      }
    }
  }

  private async consume(stream: ReadableStream<Uint8Array>): Promise<void> {
    const parser = new SseParser();
    const decoder = new TextDecoder();
    const reader = stream.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        return;
      }
      for (const parsed of parser.push(decoder.decode(value, { stream: true }))) {
        const event = JSON.parse(parsed.data) as EventEnvelope;
        if (
          !Number.isInteger(event.sequence) ||
          event.sequence <= this.lastSequence
        ) {
          continue;
        }
        this.lastSequence = event.sequence;
        this.onEvent(event);
      }
    }
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
