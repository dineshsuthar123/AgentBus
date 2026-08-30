import { describe, expect, it, vi } from "vitest";
import { StudioClient, validateStudioOrigin, type FetchLike } from "./client";

const TOKEN = "a".repeat(32);

describe("StudioClient", () => {
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
});
