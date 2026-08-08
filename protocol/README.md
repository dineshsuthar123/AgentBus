# AgentBus Control Protocol v1

This directory is generated from the Python control-plane application and
Pydantic transport models.

- `agentbus-v1.openapi.json` describes the authenticated HTTP and SSE API.
- `agentbus-v1.schema.json` contains the shared transport model definitions.
- `../extensions/vscode/src/generated/protocol.ts` is generated from the JSON Schema.

Run `agentbus control-schema export` after changing protocol models. Run
`agentbus control-schema export --check` in CI to detect stale artifacts.

The health endpoint is unauthenticated. Every `/api/v1` endpoint uses an opaque
bearer token delivered once through the daemon parent-process handshake.
Generated files contain no concrete token, credential, or machine-specific path.

Cancellation lifecycle fields are optional protocol v1 extensions with safe
defaults. Run, scheduler, report, and cancel responses share the same model.
Persisted cancellation events are monotonic and replayable; payloads contain no
prompts, bearer tokens, API keys, environment dumps, or raw provider objects.

Managed tool protocol types are also additive control v1 extensions. Generated
schemas cover tool descriptors, exact capabilities, policy decisions, resource
budgets and usage, scoped approvals, invocation results, cancellation,
artifacts, immutable audit entries, MCP server diagnostics, and the defaulted
run-report `tool_runtime` summary. The embedded managed-tool protocol is
`agentbus.tool` version `1.0`.

The control API supports bounded tool registry and policy inspection,
diagnostic-only policy evaluation, paginated run invocation and audit reads,
run-scoped tool cancellation, and configured local MCP diagnostics. It does not
expose arbitrary command execution, raw environment values, subprocess handles,
or model-controlled MCP server configuration.

Repository intelligence is an additive protocol v1 capability. Authenticated
workspace endpoints expose trust-gated, fenced, and cancellable index mutation;
bounded status and verification; paginated search and dependency reads; and
source-free symbol, impact, test-selection, and context-plan evidence. The API
does not expose raw SQLite records or unrestricted source content.

Authenticated `POST /mcp` is a constrained MCP JSON-RPC endpoint and therefore
is documented separately from the REST OpenAPI paths. It shares the same local
daemon authentication and response sanitization. See
`../docs/mcp-integration.md`.
