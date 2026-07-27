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

test("tool and MCP client routes are encoded bounded and command-free", async () => {
  const requests: Array<{
    url: string;
    method: string;
    body: BodyInit | null | undefined;
    contentType: string | null;
  }> = [];
  const client = new AgentBusClient(
    "http://127.0.0.1:43123",
    token,
    async (input, init) => {
      requests.push({
        url: String(input),
        method: init?.method ?? "GET",
        body: init?.body,
        contentType: new Headers(init?.headers).get("Content-Type")
      });
      return Response.json({});
    }
  );

  await client.tools();
  await client.tool("filesystem.read");
  await client.toolInvocations("run one", 7, 25);
  await client.toolInvocation("run one", "invoke:1");
  await client.cancelToolInvocation("run one", "invoke:1", "stop safely");
  await client.toolAudit("run one", 9, 10);
  await client.toolPolicy();
  await client.mcpServers();
  await client.checkMcpServer("local tools");

  const requestAt = (index: number) => {
    const request = requests[index];
    assert.ok(request);
    return request;
  };
  const invocationList = requestAt(2);
  const invocationDetail = requestAt(3);
  const invocationCancel = requestAt(4);
  const auditList = requestAt(5);
  const mcpCheck = requestAt(8);
  assert.equal(
    invocationList.url.endsWith(
      "/api/v1/runs/run%20one/tool-invocations?after=7&limit=25"
    ),
    true
  );
  assert.equal(
    invocationDetail.url.endsWith(
      "/api/v1/runs/run%20one/tool-invocations/invoke%3A1"
    ),
    true
  );
  assert.deepEqual(JSON.parse(String(invocationCancel.body)), {
    reason: "stop safely"
  });
  assert.equal(
    auditList.url.endsWith(
      "/api/v1/runs/run%20one/tool-audit?after=9&limit=10"
    ),
    true
  );
  assert.equal(
    mcpCheck.url.endsWith(
      "/api/v1/mcp/servers/local%20tools/check"
    ),
    true
  );
  assert.equal(mcpCheck.method, "POST");
  assert.equal(mcpCheck.body, undefined);
  assert.equal(mcpCheck.contentType, null);
  assert.throws(() => client.toolInvocations("run", -1, 10), /bounded/);
  assert.throws(() => client.toolAudit("run", 0, 501), /bounded/);
});

test("trace client routes encode identifiers and bound span pages", async () => {
  const requests: string[] = [];
  const client = new AgentBusClient(
    "http://127.0.0.1:43123",
    token,
    async (input) => {
      requests.push(String(input));
      return Response.json({});
    }
  );

  await client.trace("run one");
  await client.traceSpans("run one", 12, 25);
  await client.traceSpan("run one", "span:provider");

  assert.equal(
    requests[0]?.endsWith("/api/v1/runs/run%20one/trace"),
    true
  );
  assert.equal(
    requests[1]?.endsWith(
      "/api/v1/runs/run%20one/trace/spans?after=12&limit=25"
    ),
    true
  );
  assert.equal(
    requests[2]?.endsWith(
      "/api/v1/runs/run%20one/trace/spans/span%3Aprovider"
    ),
    true
  );
  assert.throws(() => client.traceSpans("run", 0, 501), /bounded/);
});
