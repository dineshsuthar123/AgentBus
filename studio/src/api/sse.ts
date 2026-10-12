import type { EventEnvelope } from "./types";
import { bindBrowserFetch, StudioClient } from "./client";

export class SseParser {
  private buffer = "";
  private afterCarriageReturn = false;

  public push(chunk: string): Array<{ id?: string; data: string }> {
    for (const character of chunk) {
      if (character === "\n" && this.afterCarriageReturn) {
        this.afterCarriageReturn = false;
        continue;
      }
      this.afterCarriageReturn = character === "\r";
      this.buffer += this.afterCarriageReturn ? "\n" : character;
    }
    const messages: Array<{ id?: string; data: string }> = [];
    let boundary = this.buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = this.buffer.slice(0, boundary);
      if (block.length > 1_048_576) throw new Error("SSE frame exceeds size limit");
      this.buffer = this.buffer.slice(boundary + 2);
      let id: string | undefined;
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("id:") && !line.includes("\0")) id = line.slice(3).replace(/^ /, "");
        if (line === "data") data.push("");
        if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) messages.push({ id, data: data.join("\n") });
      boundary = this.buffer.indexOf("\n\n");
    }
    if (this.buffer.length > 1_048_576) throw new Error("SSE frame exceeds size limit");
    return messages;
  }
}

export interface EventStreamOptions {
  onEvent: (event: EventEnvelope) => void;
  onState: (connected: boolean) => void;
  onStatus?: (status: StreamStatus) => void;
  onDrop?: (reason: "duplicate" | "out_of_order" | "invalid") => void;
  onReconcile?: (cursor: number) => Promise<void> | void;
  onAuthenticationFailure?: () => void;
  maximumReconnectAttempts?: number;
  fetcher?: typeof fetch;
  reconnectDelayMs?: number;
  maximumReconnectDelayMs?: number;
  jitterRatio?: number;
  random?: () => number;
}

export type StreamPhase =
  | "stopped"
  | "connecting"
  | "connected"
  | "degraded"
  | "reconnecting"
  | "restored";

export interface StreamStatus {
  connected: boolean;
  cursor: number;
  nextRetryMs?: number;
  phase: StreamPhase;
  reconnectCount: number;
}

export class StudioEventStream {
  private controller?: AbortController;
  private active = false;
  private cursor = 0;
  private reconnectCount = 0;
  private consecutiveFailures = 0;
  private reader?: ReadableStreamDefaultReader<Uint8Array>;
  private status: StreamStatus = {
    connected: false,
    cursor: 0,
    phase: "stopped",
    reconnectCount: 0
  };

  public constructor(
    private readonly client: StudioClient,
    private readonly options: EventStreamOptions
  ) {}

  public start(runId?: string): void {
    if (this.active) return;
    this.active = true;
    this.publish("connecting", false);
    void this.connect(runId);
  }

  public stop(): void {
    this.active = false;
    this.controller?.abort();
    void this.reader?.cancel().catch(() => undefined);
    this.options.onState(false);
    this.publish("stopped", false);
  }

  public get currentStatus(): StreamStatus {
    return this.status;
  }

  private async connect(runId?: string): Promise<void> {
    const fetcher = this.options.fetcher ?? bindBrowserFetch();
    while (this.active) {
      this.controller = new AbortController();
      try {
        const response = await fetcher(this.client.eventsUrl(runId, this.cursor), {
          headers: {
            Accept: "text/event-stream",
            ...(this.client.authorizationHeader() ? { Authorization: this.client.authorizationHeader() } : {}),
            ...(this.cursor ? { "Last-Event-ID": String(this.cursor) } : {})
          },
          signal: this.controller.signal
        });
        if (response.status === 401 || response.status === 403) {
          this.stop();
          this.options.onAuthenticationFailure?.();
          return;
        }
        if (!response.ok || !response.body) throw new Error("SSE unavailable");
        this.options.onState(true);
        if (this.reconnectCount > 0) {
          this.publish("restored", true);
          await this.options.onReconcile?.(this.cursor);
        }
        this.publish("connected", true);
        await this.consume(response.body);
        if (this.active) throw new Error("SSE stream ended");
      } catch {
        this.options.onState(false);
        if (!this.active || this.controller.signal.aborted) return;
        this.publish("degraded", false);
        this.consecutiveFailures += 1;
        if (this.consecutiveFailures >= (this.options.maximumReconnectAttempts ?? 8)) {
          this.active = false;
          return;
        }
        this.reconnectCount += 1;
        const delayMs = this.reconnectDelay();
        this.publish("reconnecting", false, delayMs);
        await abortableDelay(delayMs, this.controller.signal);
      }
    }
  }

  private async consume(stream: ReadableStream<Uint8Array>): Promise<void> {
    const parser = new SseParser();
    const decoder = new TextDecoder();
    const reader = stream.getReader();
    this.reader = reader;
    try {
      while (this.active) {
      const { done, value } = await reader.read();
      if (done) return;
      for (const message of parser.push(decoder.decode(value, { stream: true }))) {
        let event: EventEnvelope;
        try {
          event = JSON.parse(message.data) as EventEnvelope;
        } catch {
          this.options.onDrop?.("invalid");
          continue;
        }
        if (!isEventEnvelope(event) || (message.id && Number(message.id) !== event.sequence)) {
          this.options.onDrop?.("invalid");
          continue;
        }
        if (event.sequence <= this.cursor) {
          this.options.onDrop?.(event.sequence === this.cursor ? "duplicate" : "out_of_order");
          continue;
        }
        if (this.cursor > 0 && event.sequence > this.cursor + 1) await this.options.onReconcile?.(this.cursor);
        this.consecutiveFailures = 0;
        this.cursor = event.sequence;
        this.status = { ...this.status, cursor: this.cursor };
        this.options.onEvent(event);
      }
      }
    } finally {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
      this.reader = undefined;
    }
  }

  private reconnectDelay(): number {
    const base = Math.max(10, this.options.reconnectDelayMs ?? 500);
    const maximum = Math.max(base, this.options.maximumReconnectDelayMs ?? 15_000);
    const exponential = Math.min(maximum, base * (2 ** Math.min(this.reconnectCount - 1, 8)));
    const ratio = Math.min(0.5, Math.max(0, this.options.jitterRatio ?? 0.2));
    const random = this.options.random?.() ?? Math.random();
    const jitter = 1 - ratio + (Math.min(1, Math.max(0, random)) * ratio * 2);
    return Math.round(exponential * jitter);
  }

  private publish(phase: StreamPhase, connected: boolean, nextRetryMs?: number): void {
    this.status = Object.freeze({
      connected,
      cursor: this.cursor,
      nextRetryMs,
      phase,
      reconnectCount: this.reconnectCount
    });
    this.options.onStatus?.(this.status);
  }
}

function isEventEnvelope(event: EventEnvelope): boolean {
  return Number.isInteger(event.sequence)
    && event.sequence > 0
    && typeof event.event_type === "string"
    && event.event_type.length > 0
    && typeof event.timestamp === "string";
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = window.setTimeout(finish, milliseconds);
    signal.addEventListener("abort", finish, { once: true });
    function finish() {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    }
  });
}
