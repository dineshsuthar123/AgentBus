import assert from "node:assert/strict";
import test from "node:test";
import type {
  McpServerCheckResponse,
  McpServerSummary
} from "../generated/protocol";
import {
  formatMcpServer,
  formatMcpServerCheck,
  formatMcpServerTooltip
} from "../mcpPresentation";

function server(): McpServerSummary {
  return {
    server_id: "local-fixture",
    transport: "stdio",
    executable_alias: "fixture-mcp",
    configured_tools: [
      {
        name: "write_note",
        namespaced_name: "mcp.local-fixture.write_note",
        capabilities: [
          { name: "mcp.invoke", scope: { mcp_servers: ["local-fixture"] } }
        ]
      }
    ],
    supported_protocol_versions: ["2025-06-18"],
    startup_timeout_seconds: 5,
    request_timeout_seconds: 10
  };
}

test("MCP configuration presentation exposes only safe aliases and capabilities", () => {
  const summary = {
    ...server(),
    environment: { API_KEY: "private-value" },
    command: ["private-command"]
  } as McpServerSummary;
  const document = formatMcpServer(summary);
  const tooltip = formatMcpServerTooltip(summary);

  assert.match(document, /fixture\\-mcp/);
  assert.match(document, /mcp\\\.invoke/);
  assert.match(tooltip, /stdio/);
  assert.doesNotMatch(document, /private-value|private-command/);
});

test("MCP check presentation redacts diagnostics and escapes Markdown", () => {
  const response: McpServerCheckResponse = {
    server: server(),
    ready: false,
    checked_at: "2026-01-01T00:00:00Z",
    diagnostic_timeout_seconds: 10,
    server_name: "[fixture](command:evil)",
    capabilities: ["tools"],
    advertised_tools: ["write_note"],
    cleanup_completed: true,
    message: "Bearer private-token"
  };
  const rendered = formatMcpServerCheck(response);

  assert.match(rendered, /Cleanup completed.*yes/);
  assert.match(rendered, /Bearer \\\[REDACTED\\\]/);
  assert.doesNotMatch(rendered, /private-token/);
  assert.doesNotMatch(rendered, /\[fixture\]\(command:evil\)/);
});
