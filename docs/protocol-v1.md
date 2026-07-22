# Control Protocol v1

The authoritative artifacts are:

- `protocol/agentbus-v1.openapi.json`
- `protocol/agentbus-v1.schema.json`
- `extensions/vscode/src/generated/protocol.ts`

They are generated from the FastAPI application and Pydantic models. Run
`agentbus control-schema export` after model changes and
`agentbus control-schema export --check` in verification.

All errors use `{ "error": { "code", "message", "retryable", "details" } }`.
SSE event IDs are persisted monotonic SQLite IDs. Clients discard duplicate or
stale sequences and reconnect with `Last-Event-ID`.

Cancellation lifecycle fields are backward-compatible protocol v1 extensions:
all nested objects have defaults, and older payloads without `cancellation`
remain valid. `RunSummary`, `SchedulerResponse`, `RunReportResponse`, and
`CancelResponse` expose the same typed lifecycle. The report body additionally
retains bounded execution diagnostics for CLI and support inspection.

Cancellation events are:

- `cancellation_requested`
- `cancellation_propagated`
- `provider_cancellation_requested`
- `provider_cancellation_acknowledged`
- `operation_completed_after_cancellation`
- `scheduling_stopped`
- `run_cancelled`
- `cancellation_cleanup_completed`

Payloads contain lifecycle facts, repository-relative identifiers, and bounded
labels only. They do not contain prompts, bearer tokens, API keys, environment
dumps, or raw provider objects.

## Managed tool extensions

Tool runtime models are backward-compatible protocol v1 additions. They use the
separate `agentbus.tool` protocol version `1.0` inside control responses. The
control schema exposes frozen capability, policy, budget, result,
cancellation, artifact, approval, and audit records without exposing tool
implementations or raw process state.

Authenticated discovery and diagnostics endpoints are:

- `GET /api/v1/tools`
- `GET /api/v1/tools/{tool_name}`
- `GET /api/v1/policy`
- `POST /api/v1/policy/evaluate`
- `GET /api/v1/mcp/servers`
- `POST /api/v1/mcp/servers/{server_id}/check`

Run-scoped inspection and cancellation endpoints are:

- `GET /api/v1/runs/{run_id}/tool-invocations`
- `GET /api/v1/runs/{run_id}/tool-invocations/{invocation_id}`
- `POST /api/v1/runs/{run_id}/tool-invocations/{invocation_id}/cancel`
- `GET /api/v1/runs/{run_id}/tool-audit`

Invocation and audit lists are ordered by immutable sequence and use bounded
`after` and `limit` pagination. Tool cancellation requests cancellation for the
owning run rather than terminating an unrelated process from an API-supplied
PID. Policy evaluation is diagnostic only: it is not persisted, does not
execute a tool, and cannot create an approval.

Tool approvals appear in the existing run approval list with
`approval_kind: "tool"`. Their revision, descriptor, capabilities, redacted
argument summary, executable, working directory, network destination, policy
rule, constraints, and resource budget are safe protocol fields. The existing
approval decision endpoint remains revision checked and idempotent.

Tool lifecycle SSE events include `tool_invocation_requested`,
`tool_policy_allowed`, `tool_policy_denied`, `tool_approval_required`,
`tool_approval_approved`, `tool_approval_rejected`,
`tool_invocation_started`, terminal invocation events, and
`tool_audit_recorded`. Payloads contain identifiers, hashes, capability names,
status, bounded usage, and safe diagnostics. They do not contain complete raw
arguments, environments, credentials, or unbounded output.

Run reports add a defaulted `tool_runtime` object with invocation and status
counts, budget snapshots, cancellation facts, MCP usage, and audit coverage.
Older records without tool activity remain valid.

## MCP endpoint

Authenticated `POST /mcp` exposes the constrained AgentBus MCP JSON-RPC server.
It is not an arbitrary command endpoint and is not under `/api/v1` because MCP
uses its own negotiated protocol versions. It shares the daemon's numeric
loopback binding and opaque bearer authentication. See
[MCP Integration](mcp-integration.md) for its fixed tool list and limits.
