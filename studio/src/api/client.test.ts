import { describe, expect, it, vi } from "vitest";
import { StudioClient, validateStudioOrigin, type FetchLike } from "./client";

const TOKEN = "a".repeat(32);

describe("StudioClient", () => {
  it("binds the native browser fetch receiver", async () => {
    const browserFetch = vi.fn(function (this: unknown) {
      if (this !== window) throw new TypeError("Illegal invocation");
      return Promise.resolve(new Response(JSON.stringify({ protocol_version: "1.0" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      }));
    });
    vi.stubGlobal("fetch", browserFetch);

    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173");
    await expect(client.info()).resolves.toEqual({ protocol_version: "1.0" });
    expect(browserFetch).toHaveBeenCalledOnce();

    vi.unstubAllGlobals();
  });

  it("accepts numeric loopback origins and rejects hostname aliases", () => {
    expect(validateStudioOrigin("http://127.0.0.1:5173").origin).toBe("http://127.0.0.1:5173");
    expect(validateStudioOrigin("http://[::1]:5173").origin).toBe("http://[::1]:5173");
    expect(() => validateStudioOrigin("http://localhost:5173")).toThrow(/numeric loopback/i);
    expect(() => validateStudioOrigin("https://127.0.0.1:5173")).toThrow(/numeric loopback/i);
  });

  it("keeps authorization in the request header", async () => {
    const fetcher = vi.fn<FetchLike>(async () => new Response(JSON.stringify({ protocol_version: "1" }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    }));
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);

    await client.info();

    const [request, init] = fetcher.mock.calls[0];
    expect(String(request)).toBe("http://127.0.0.1:5173/api/v1/info");
    expect(init?.headers).toMatchObject({ Authorization: `Bearer ${TOKEN}` });
    expect(String(request)).not.toContain(TOKEN);
  });

  it("maps bounded control-plane errors without exposing an unknown response body", async () => {
    const fetcher = vi.fn<FetchLike>(async () => new Response(JSON.stringify({
      error: { code: "workspace_mismatch", message: "Workspace repository does not match.", retryable: false },
      secret_debug_body: "must not surface"
    }), { status: 409, headers: { "Content-Type": "application/json" } }));
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);

    await expect(client.runs()).rejects.toMatchObject({
      name: "StudioApiError",
      code: "workspace_mismatch",
      message: "Workspace repository does not match.",
      status: 409,
      retryable: false
    });
  });

  it("requires a non-trivial one-time token", () => {
    expect(() => new StudioClient("short", "http://127.0.0.1:5173")).toThrow(/at least 32/);
  });

  it("deduplicates overlapping reads and serves a bounded fresh cache", async () => {
    let release: ((response: Response) => void) | undefined;
    const fetcher = vi.fn<FetchLike>(() => new Promise<Response>((resolve) => { release = resolve; }));
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);

    const first = client.run("run-1");
    const second = client.run("run-1");
    expect(fetcher).toHaveBeenCalledOnce();
    release!(new Response(JSON.stringify({ run_id: "run-1", status: "running" }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    }));

    await expect(Promise.all([first, second])).resolves.toEqual([
      { run_id: "run-1", status: "running" },
      { run_id: "run-1", status: "running" }
    ]);
    await client.run("run-1");
    expect(fetcher).toHaveBeenCalledOnce();
    expect(client.queryCacheSummary()).toMatchObject({
      cachedReads: 1,
      cacheHits: 1,
      deduplicatedReads: 1,
      inflightReads: 0
    });
  });

  it("aborts an obsolete route read when it has no remaining consumers", async () => {
    let requestSignal: AbortSignal | undefined;
    const fetcher = vi.fn<FetchLike>((_input, init) => {
      requestSignal = init?.signal as AbortSignal;
      return new Promise<Response>((_resolve, reject) => {
        requestSignal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    });
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);
    const route = new AbortController();

    const request = client.run("run-obsolete", { signal: route.signal, force: true });
    route.abort();

    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(requestSignal?.aborted).toBe(true);
  });

  it("replaces an aborted in-flight read before its cleanup microtask settles", async () => {
    let secondResponse: ((response: Response) => void) | undefined;
    const fetcher = vi.fn<FetchLike>((_input, init) => {
      const signal = init?.signal as AbortSignal;
      if (fetcher.mock.calls.length === 1) {
        return new Promise<Response>((_resolve, reject) => {
          signal.addEventListener("abort", () => queueMicrotask(() => reject(new DOMException("aborted", "AbortError"))));
        });
      }
      return new Promise<Response>((resolve) => { secondResponse = resolve; });
    });
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);
    const obsolete = new AbortController();

    const first = client.run("run-remounted", { signal: obsolete.signal, force: true });
    obsolete.abort();
    const remounted = client.run("run-remounted", { force: true });
    secondResponse!(new Response(JSON.stringify({ run_id: "run-remounted", status: "running" }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    }));

    await expect(first).rejects.toMatchObject({ name: "AbortError" });
    await expect(remounted).resolves.toMatchObject({ run_id: "run-remounted" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(client.queryCacheSummary().inflightReads).toBe(0);
  });

  it("never retries a non-idempotent approval mutation", async () => {
    const fetcher = vi.fn<FetchLike>(async () => new Response(JSON.stringify({
      error: { code: "temporarily_unavailable", message: "Try later.", retryable: true }
    }), { status: 503, headers: { "Content-Type": "application/json" } }));
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);

    await expect(client.decideApproval("run-1", "approval-1", 3, "approve")).rejects.toMatchObject({
      code: "temporarily_unavailable"
    });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("does not dispatch a read whose consumer was already cancelled", async () => {
    const fetcher = vi.fn<FetchLike>();
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);
    const controller = new AbortController();
    controller.abort();

    await expect(client.run("obsolete", { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("bounds cached query identities across long browsing sessions", async () => {
    const fetcher = vi.fn<FetchLike>(async () => new Response(JSON.stringify({ run_id: "fixture" })));
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);
    for (let index = 0; index < 150; index += 1) await client.run(`run-${index}`);

    expect(client.queryCacheSummary().cachedReads).toBeLessThanOrEqual(128);
  });

  it("does not cache large file responses", async () => {
    const fetcher = vi.fn<FetchLike>(async () => new Response(JSON.stringify({ content: "x".repeat(600_000) })));
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);
    await client.file("run", "large.txt", "after");

    expect(client.queryCacheSummary().cachedReads).toBe(0);
  });

  it("invalidates reads after a mutation without replaying the action", async () => {
    let status = "running";
    const fetcher = vi.fn<FetchLike>(async (_url, init) => {
      if (init?.method === "POST") status = "cancelled";
      return new Response(JSON.stringify({ run_id: "run", status }));
    });
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);
    await client.run("run");
    await client.cancel("run", "Explicit cancellation");

    await expect(client.run("run")).resolves.toMatchObject({ status: "cancelled" });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("never lets a pre-invalidation response repopulate the cache", async () => {
    let release: (response: Response) => void = () => undefined;
    const fetcher = vi.fn<FetchLike>()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }))
      .mockImplementation(async () => new Response(JSON.stringify({ status: "succeeded" })));
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);
    const older = client.run("run");
    client.invalidate();
    release(new Response(JSON.stringify({ status: "running" })));
    await older;

    await expect(client.run("run")).resolves.toMatchObject({ status: "succeeded" });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("disposes pending reads, cached data, and authorization together", async () => {
    const fetcher = vi.fn<FetchLike>((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);
    const pending = client.run("run");
    client.dispose();

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(client.authorizationHeader()).toBe("");
    expect(client.queryCacheSummary().cachedReads).toBe(0);
    await expect(client.run("run")).rejects.toMatchObject({ code: "session_closed" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("handles a malformed error envelope with a safe HTTP fallback", async () => {
    const fetcher = vi.fn<FetchLike>(async () => new Response("null", { status: 409 }));
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);

    await expect(client.cancel("run", "stop")).rejects.toMatchObject({ code: "http_error", status: 409 });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("bounds the total serialized cache footprint as well as its entry count", async () => {
    const fetcher = vi.fn<FetchLike>(async () => new Response(JSON.stringify({ content: "x".repeat(100_000) })));
    const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);
    for (let index = 0; index < 30; index += 1) await client.file("run", `file-${index}.txt`, "after");

    expect(client.queryCacheSummary().cachedBytes).toBeLessThanOrEqual(4 * 1024 * 1024);
    expect(client.queryCacheSummary().cachedReads).toBeGreaterThan(0);
  });

  it("reports an ambiguous mutation timeout without replaying the action", async () => {
    vi.useFakeTimers();
    try {
      const fetcher = vi.fn<FetchLike>((_url, init) => new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      }));
      const client = new StudioClient(TOKEN, "http://127.0.0.1:5173", fetcher);
      const result = expect(client.cancel("run", "Explicit cancellation")).rejects.toMatchObject({
        code: "request_timeout", status: 408, retryable: false,
        message: expect.stringContaining("the action was not retried"),
      });
      await vi.advanceTimersByTimeAsync(30_000);
      await result;
      expect(fetcher).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });
});
