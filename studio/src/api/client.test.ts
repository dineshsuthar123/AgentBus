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
});
