import type { EventEnvelope } from "./generated/protocol";
import type { SyndraClient } from "./apiClient";

export class SseParser {
  private buffer = "";
  private afterCarriageReturn = false;
  private eventType = "message";
  private eventId: string | undefined;
  private data: string[] = [];

  public push(chunk: string): Array<{
    id?: string;
    event: string;
    data: string;
  }> {
    for (const character of chunk) {
      if (character === "\n" && this.afterCarriageReturn) {
        this.afterCarriageReturn = false;
        continue;
      }
      this.afterCarriageReturn = character === "\r";
      this.buffer += this.afterCarriageReturn ? "\n" : character;
    }
    const output: Array<{ id?: string; event: string; data: string }> = [];
    let boundary = this.buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = this.buffer.slice(0, boundary);
      if (block.length > 1_048_576) throw new Error("SSE frame exceeds size limit");
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
        if (field === "id" && !value.includes("\0")) {
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
    if (this.buffer.length > 1_048_576) throw new Error("SSE frame exceeds size limit");
    return output;
  }
}

export interface SseClientOptions {
  reconnectDelayMs?: number;
  onReconnectFailure?: () => Promise<void>;
  onAuthenticationFailure?: () => void;
  onRetryExhausted?: () => void;
  maximumReconnectAttempts?: number;
  fetcher?: typeof fetch;
}

export class ReconnectingSseClient {
  private controller: AbortController | undefined;
  private running = false;
  private connected = false;
  private lastSequence = 0;
  private consecutiveFailures = 0;
  private reader: ReadableStreamDefaultReader<Uint8Array> | undefined;

  public constructor(
    private readonly client: SyndraClient,
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
    this.connected = false;
    this.controller?.abort();
    void this.reader?.cancel().catch(() => undefined);
  }

  public get cursor(): number {
    return this.lastSequence;
  }

  public get isConnected(): boolean {
    return this.connected;
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
        if (response.status === 401 || response.status === 403) {
          this.stop();
          this.options.onAuthenticationFailure?.();
          return;
        }
        if (!response.ok || !response.body) {
          throw new Error(`SSE connection failed with HTTP ${response.status}.`);
        }
        this.connected = true;
        await this.consume(response.body);
        this.connected = false;
        if (this.running) throw new Error("SSE stream ended.");
      } catch {
        this.connected = false;
        if (!this.running || this.controller.signal.aborted) {
          return;
        }
        this.consecutiveFailures += 1;
        if (this.consecutiveFailures >= (this.options.maximumReconnectAttempts ?? 8)) {
          this.running = false;
          this.options.onRetryExhausted?.();
          return;
        }
        try {
          await this.options.onReconnectFailure?.();
        } catch {
          // The daemon may still be restarting; the next reconnect retries safely.
        }
        await delay(Math.min(15_000, (this.options.reconnectDelayMs ?? 1_000) * (2 ** (this.consecutiveFailures - 1))), this.controller.signal);
      }
    }
  }

  private async consume(stream: ReadableStream<Uint8Array>): Promise<void> {
    const parser = new SseParser();
    const decoder = new TextDecoder();
    const reader = stream.getReader();
    this.reader = reader;
    try {
      while (this.running) {
      const { done, value } = await reader.read();
      if (done) {
        return;
      }
      for (const parsed of parser.push(decoder.decode(value, { stream: true }))) {
        let event: EventEnvelope;
        try { event = JSON.parse(parsed.data) as EventEnvelope; } catch { continue; }
        if (
          !event || typeof event.event_type !== "string" || typeof event.timestamp !== "string" ||
          (parsed.id && Number(parsed.id) !== event.sequence) ||
          !Number.isInteger(event.sequence) ||
          event.sequence <= this.lastSequence
        ) {
          continue;
        }
        if (this.lastSequence > 0 && event.sequence > this.lastSequence + 1) await this.options.onReconnectFailure?.();
        this.consecutiveFailures = 0;
        this.lastSequence = event.sequence;
        this.onEvent(event);
      }
      }
    } finally {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
      this.reader = undefined;
    }
  }
}

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) { resolve(); return; }
    const timer = setTimeout(finish, milliseconds);
    signal.addEventListener("abort", finish, { once: true });
    function finish() {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    }
  });
}
