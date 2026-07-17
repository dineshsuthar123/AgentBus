import assert from "node:assert/strict";
import test from "node:test";
import { AgentBusApiError, AgentBusClient } from "../apiClient";

const token = "a-test-token-that-is-more-than-thirty-two-bytes";

test("API client authenticates in headers without token URLs", async () => {
  let observedUrl = "";
  let observedAuthorization = "";
  const client = new AgentBusClient(
    "http://127.0.0.1:43123",
    token,
    async (input, init) => {
      observedUrl = String(input);
      observedAuthorization = new Headers(init?.headers).get("Authorization") ?? "";
      return Response.json({
        protocol_version: "1.0",
        agentbus_version: "0.2",
        daemon_id: "daemon",
        pid: 1,
        host: "127.0.0.1",
        port: 43123,
        started_at: "2026-01-01T00:00:00Z",
        state_database: "state.db"
      });
    }
  );

  await client.info();

  assert.equal(observedAuthorization, `Bearer ${token}`);
  assert.equal(observedUrl.includes(token), false);
});

test("API client maps stable safe errors", async () => {
  const client = new AgentBusClient(
    "http://127.0.0.1:43123",
    token,
    async () =>
      Response.json(
        {
          error: {
            code: "conflict",
            message: "Workspace is busy.",
            retryable: true,
            details: {}
          }
        },
        { status: 409 }
      )
  );

  await assert.rejects(client.listRuns(), (error: unknown) => {
    assert.ok(error instanceof AgentBusApiError);
    assert.equal(error.code, "conflict");
    assert.equal(error.retryable, true);
    return true;
  });
});

test("API client rejects remote and credentialed daemon URLs", () => {
  assert.throws(
    () => new AgentBusClient("http://example.com:43123", token),
    /loopback/
  );
  assert.throws(
    () => new AgentBusClient("http://user:pass@127.0.0.1:43123", token),
    /loopback/
  );
});
