import type {
  McpServerCheckResponse,
  McpServerSummary,
  ToolCapability
} from "./generated/protocol";
import { redactText } from "./redaction";
import { escapeMarkdown } from "./toolPresentation";

export function formatMcpServerTooltip(server: McpServerSummary): string {
  return [
    `**${safeMarkdown(server.server_id)}**`,
    `Transport: \`${safeMarkdown(server.transport)}\``,
    `Executable alias: \`${safeMarkdown(server.executable_alias ?? "none")}\``,
    `Endpoint host: \`${safeMarkdown(server.endpoint_host ?? "none")}\``,
    `Configured tools: ${server.configured_tools.length}`,
    `Protocols: ${safeMarkdown(server.supported_protocol_versions.join(", ") || "none")}`,
    `Startup timeout: ${server.startup_timeout_seconds}s`,
    `Request timeout: ${server.request_timeout_seconds}s`
  ].join("\n\n");
}

export function formatMcpServer(server: McpServerSummary): string {
  const tools = server.configured_tools.map((tool) => [
    tool.name,
    tool.namespaced_name,
    capabilitySummary(tool.capabilities)
  ]);
  return [
    `# MCP Server ${safeMarkdown(server.server_id)}`,
    "",
    markdownTable(
      [
        ["Transport", server.transport],
        ["Executable alias", server.executable_alias ?? "none"],
        ["Endpoint host", server.endpoint_host ?? "none"],
        ["Supported protocols", server.supported_protocol_versions.join(", ")],
        ["Startup timeout", `${server.startup_timeout_seconds}s`],
        ["Request timeout", `${server.request_timeout_seconds}s`]
      ],
      ["Field", "Value"]
    ),
    "",
    "## Configured Tools",
    "",
    tools.length
      ? markdownTable(tools, ["Tool", "Managed Name", "Capabilities"])
      : "No tools are configured.",
    "",
    "_Commands, arguments, inherited environments, credentials, and raw server output are not displayed._",
    ""
  ].join("\n");
}

export function formatMcpServerCheck(
  response: McpServerCheckResponse
): string {
  return [
    `# MCP Check ${safeMarkdown(response.server.server_id)}`,
    "",
    markdownTable(
      [
        ["Ready", response.ready ? "yes" : "no"],
        ["Checked", response.checked_at],
        ["Diagnostic timeout", `${response.diagnostic_timeout_seconds}s`],
        ["Protocol", response.protocol_version ?? "not negotiated"],
        ["Server name", response.server_name ?? "not reported"],
        ["Server version", response.server_version ?? "not reported"],
        ["Tool count", response.tool_count ?? 0],
        ["Cleanup completed", response.cleanup_completed ? "yes" : "no"],
        ["Message", response.message ?? "none"]
      ],
      ["Field", "Value"]
    ),
    "",
    "## Capabilities",
    "",
    bulletList(response.capabilities ?? []),
    "",
    "## Advertised Tools",
    "",
    bulletList(response.advertised_tools ?? []),
    "",
    "_The diagnostic performs only the configured local handshake, ping, and tool listing. Raw server output is not displayed._",
    ""
  ].join("\n");
}

function capabilitySummary(capabilities: ToolCapability[]): string {
  return capabilities.map((value) => value.name).join(", ") || "none";
}

function markdownTable(
  rows: Array<Array<string | number>>,
  headers: string[]
): string {
  const lines = [
    `| ${headers.map(safeMarkdown).join(" | ")} |`,
    `| ${headers.map(() => "---").join(" | ")} |`
  ];
  for (const row of rows.slice(0, 256)) {
    lines.push(
      `| ${row.map((value) => safeMarkdown(value)).join(" | ")} |`
    );
  }
  return lines.join("\n");
}

function bulletList(values: string[]): string {
  if (values.length === 0) return "None reported.";
  return values
    .slice(0, 256)
    .map((value) => `- ${safeMarkdown(value)}`)
    .join("\n");
}

function safeMarkdown(value: unknown): string {
  return escapeMarkdown(
    redactText(value, 512).replace(/[\r\n]+/g, " ")
  );
}
