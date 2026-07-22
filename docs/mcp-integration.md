# MCP Integration

AgentBus supports explicitly configured local Model Context Protocol servers
and exposes a separate constrained AgentBus MCP endpoint. MCP peers are
untrusted. A server name, local process, schema, or successful handshake never
grants tool authority by itself.

There is no automatic MCP discovery, public remote transport, model-supplied
server command, or unrestricted plugin loading.

## Client configuration

MCP servers are configured only through an explicitly selected JSON or TOML
AgentBus config file. At most 64 server IDs may be configured, and IDs must be
unique lowercase ASCII identifiers. Each configured tool requires an explicit
capability map.

This JSON example configures a local stdio server through the existing
allowlisted `python` executable alias:

```json
{
  "agentbus": {
    "mcp_server_configs": [
      {
        "server_id": "local_tools",
        "transport": "stdio",
        "executable_alias": "python",
        "arguments": ["-m", "example_mcp_server"],
        "working_directory": ".",
        "environment": {
          "NO_COLOR": "1"
        },
        "capability_map": {
          "echo": [
            {
              "name": "mcp.connect",
              "scope": {"mcp_servers": ["local_tools"]}
            },
            {
              "name": "mcp.invoke",
              "scope": {"mcp_servers": ["local_tools"]}
            }
          ]
        }
      }
    ]
  }
}
```

Additional capabilities must describe the actual effect of the remote tool.
For example, an MCP tool that writes a project file also needs the exact
`filesystem.write` or `filesystem.create` scope. The transport capabilities
must be scoped only to the configured server ID.

Imported names are deterministic:

```text
mcp.<server-id>.<lowercase-tool-name>
```

Names, normalized namespaces, and registered built-ins cannot collide. Every
advertised tool must have a configured map, and every configured tool must be
advertised during discovery.

## Stdio transport

A stdio server requires:

- an executable alias already present in the trusted executable catalog;
- at most 64 bounded, single-line, NUL-free arguments;
- a repository-relative working directory inside the assigned worktree;
- no inherited unrestricted environment;
- only the explicit safe overrides `CI`, `LANG`, `LC_ALL`, `NO_COLOR`, and
  `PYTHONDONTWRITEBYTECODE`;
- bounded startup, request, server-output, and tool-output limits.

The executable identity is resolved and revalidated before launch. Stdio
servers run with `shell=False`, an isolated home and temporary directory, a
sanitized environment, and the same Windows Job Object or POSIX process-group
supervision as managed processes. Windows `.cmd` and `.bat` MCP servers are
refused; configure a native executable or an allowlisted interpreter instead.

Closing a session closes stdin, terminates the process tree if it does not exit
within the bounded grace period, joins reader threads, closes handles, and
removes the isolated temporary home. Startup and protocol failures run the
same cleanup path.

## Authenticated loopback HTTP

Install the optional dependency with `pip install "agentbus[mcp]"` before using
`loopback_http`. This transport requires all of the following:

- `explicit_loopback_http: true`;
- an `http` or `https` URL using numeric `127.0.0.1` or `::1` only;
- no URL username, password, query, or fragment;
- a bearer token of at least 16 characters;
- no local executable, command arguments, or environment overrides.

The HTTP client sends authorization in a header, ignores proxy environment,
does not follow redirects, bounds response bytes and message counts, validates
content types and session IDs, and can close an active request on cancellation.
The token is held as a secret value and omitted from representations,
diagnostics, generated schemas, reports, and normal configuration output.
An explicit config file is not a secrets manager: if it contains the token,
protect that file with OS permissions, keep it outside source control, and do
not share it as a diagnostic artifact.

The numeric-host rule avoids DNS and hosts-file ambiguity. AgentBus does not
support LAN, public internet, Unix-socket, named-pipe, or unauthenticated MCP
HTTP endpoints.

## Negotiation and schema validation

Supported MCP protocol versions are `2025-11-25` and `2025-06-18`. The client
offers configured versions, verifies the server-selected version, sends the
initialized notification, and requires the server to advertise the tools
capability. A downgrade outside the configured supported set fails closed.

Tool discovery is limited to 16 pages and 256 tools. Descriptions, server
metadata, cursors, schemas, annotations, content arrays, JSON-RPC messages, and
results are bounded. Imported input and output schemas must be valid object
JSON Schemas, have bounded depth, and cannot contain `$ref` or `$dynamicRef`.
Arguments are validated locally before `tools/call`; successful structured
output is validated locally after the response.

JSON-RPC version and request IDs must match exactly. Malformed errors,
collisions, invalid UTF-8, non-finite JSON, unsupported content types,
oversized output, repeated cursors, and protocol changes terminate the call
safely. Remote error text is bounded and redacted.

## Policy and approvals

An imported tool becomes a normal `ToolDescriptor` in the managed registry.
The model must request the exact independently derived capability set. The
default policy requires exact approval for every `mcp.connect` or `mcp.invoke`
call, even when the server is local and configured.

The approval binds server ID, namespaced tool, arguments hash, complete
capability map, run, task, workspace, worktree, resource budget, protocol and
tool versions, cancellation revision, and invocation revision. Approval of one
MCP call cannot authorize another tool, server, argument set, task, or retry
revision. Other effects, such as filesystem or process capabilities, continue
through normal capability and policy validation after approval. Those declared
scopes cannot prove that a compromised MCP peer obeys them; run local peers
with the same least-privilege OS assumptions as generated code.

MCP invocation counts, server IDs, statuses, bounded results, cancellation,
policy decisions, and audit records appear in the normal run report. Raw
authorization tokens, complete remote output, and server environments do not.

## Diagnostics

The authenticated control plane provides:

- `GET /api/v1/mcp/servers` for redacted configured-server summaries;
- `POST /api/v1/mcp/servers/{server_id}/check` for a bounded connect, ping, and
  discovery check;
- regular tool registry, invocation, approval, cancellation, and audit APIs for
  imported calls.

Diagnostics identify the configured alias, transport, endpoint host, negotiated
protocol, advertised namespaced tools, output limits, and cleanup state. They
never return the command path supplied by a remote caller, raw environment
values, or an HTTP bearer token. A check creates a temporary session and closes
it before returning.

VS Code exposes configured servers in the MCP Servers view with `AgentBus:
Show MCP Server` and `AgentBus: Check MCP Server`. Imported calls appear in the
Tool Invocations and Approvals views and use the same exact approval commands.

## AgentBus MCP server

The local AgentBus daemon exposes MCP JSON-RPC at authenticated `POST /mcp`.
It reuses the loopback-only daemon bearer authentication, repository services,
path validation, run supervisor, response models, and redaction. The maximum
batch is 64 requests and the maximum encoded tool result is 1,000,000 bytes.

Exposed tools are:

- `agentbus.run.inspect`
- `agentbus.run.tasks`
- `agentbus.run.report`
- `agentbus.run.approvals`
- `agentbus.run.changes`
- `agentbus.run.diff`
- `agentbus.tools.inspect`
- `agentbus.run.cancel`
- `agentbus.run.submit`

Submission is deliberately narrow: deterministic provider, durable
multi-agent workflow, one worker, no parallel execution, no fallback, no live
provider consent, no commit, no PR, and only the `python-calculator` or
`cancellation-two-task` profiles. Cancellation is a cooperative request. The
server does not expose approval decisions.

The AgentBus MCP server does not expose arbitrary files, arbitrary process or
Git execution, raw SQLite, hidden prompts, provider credentials, daemon tokens,
daemon stop, remote binding, push, commit, PR creation, or unrestricted source
extraction.

## Limitations

- A local MCP process executes with the AgentBus OS account's permissions; the
  capability layer is not kernel isolation.
- Loopback HTTP authenticates the configured peer but does not make its output
  trustworthy.
- MCP effects outside managed AgentBus adapters cannot be transactionally
  rolled back after failure or cancellation.
- Imported tools must be configured in advance; dynamic remote capability
  expansion is intentionally unsupported.
- Passing offline MCP fixtures does not establish the safety of another MCP
  implementation.
