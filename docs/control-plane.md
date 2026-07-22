# Local Control Plane

AgentBus v0.3 keeps a thin FastAPI adapter over the existing orchestrator,
StateStore, approval engine, lease service, worktree manager, and GitRepository.
It does not implement a second scheduler or execution database.

The daemon binds a pre-allocated loopback socket, writes non-secret discovery
metadata, and delivers one opaque session token to its parent process. Runs are
submitted to a bounded background supervisor. Durable state, task attempts,
events, approvals, cancellation, leases, and reports remain authoritative in
SQLite.

The API is versioned under `/api/v1`. `/health` is the only unauthenticated
endpoint. Events use persisted SQLite event IDs for ordered SSE replay and
`Last-Event-ID` reconnection.

Filesystem edits are not rolled back when a run fails. Reports list retained
changes and cleanup recommendations; AgentBus never resets or cleans user work.

## Managed tools

The control plane exposes safe inspection for the capability-based tool
runtime. It does not expose an arbitrary command or model-controlled server
configuration endpoint.

Registry and policy routes are:

- `GET /api/v1/tools`
- `GET /api/v1/tools/{tool_name}`
- `GET /api/v1/policy`
- `POST /api/v1/policy/evaluate`

Policy evaluation validates a registered descriptor, derives exact
capabilities locally, and returns a diagnostic decision. It does not execute a
tool, persist an invocation, or create an approval.

Run tool routes are:

- `GET /api/v1/runs/{run_id}/tool-invocations`
- `GET /api/v1/runs/{run_id}/tool-invocations/{invocation_id}`
- `POST /api/v1/runs/{run_id}/tool-invocations/{invocation_id}/cancel`
- `GET /api/v1/runs/{run_id}/tool-audit`

List routes use immutable sequence pagination. Responses include exact
capabilities, policy outcomes, approval references, budgets, usage,
cancellation, bounded results, artifacts, hashes, and safe errors. They omit
raw environments, subprocess handles, approval secrets, and unbounded output.

Tool cancellation requests cancellation for the invocation's owning run. It
never accepts a PID from the client or signals an unrelated process.

Tool approvals use the existing run approval list and decision routes with
`approval_kind: tool`. The request includes the exact invocation revision,
descriptor, capabilities, paths, executable, redacted argument summary,
working directory, network destination, policy rule, and budget. The decision
is revision checked and idempotent. Resume revalidates the complete approval
binding before execution.

Run reports include a `tool_runtime` summary with invocation status counts,
budget state, audit coverage, cancellation, and MCP usage. Failed reports still
list created and modified files.

## Local MCP

Configured local MCP diagnostics are available at:

- `GET /api/v1/mcp/servers`
- `POST /api/v1/mcp/servers/{server_id}/check`

The check performs one bounded connect, ping, and tool-discovery cycle, then
closes the client and supervised stdio process before returning. Output shows
safe aliases, endpoint hosts, negotiated versions, namespaced tool names, and
cleanup status, never bearer tokens or environment values.

Authenticated `POST /mcp` exposes AgentBus itself as a fixed, constrained MCP
server. It shares daemon authentication and path validation but does not expose
arbitrary files, process execution, SQLite, approval decisions, commit, push,
PR creation, or live provider calls. See [MCP Integration](mcp-integration.md).

## Cooperative cancellation

`POST /api/v1/runs/{run_id}/cancel` is idempotent. It records intent before
returning and never treats Python future cancellation as proof that work
stopped. Run, report, scheduler, and cancel responses expose a defaulted
`cancellation` object with:

- request, propagation, and acknowledgement timestamps;
- provider cancellation signal and acknowledgement;
- an active non-interruptible operation;
- operations and tasks completed after the request;
- tasks prevented from starting;
- scheduling and cleanup completion;
- resume eligibility and terminal reason.

The persisted event order is `cancellation_requested`,
`cancellation_propagated`, optional provider request and acknowledgement,
optional completed-after events, `scheduling_stopped`, `run_cancelled`, and
`cancellation_cleanup_completed`. Event payloads are bounded and sanitized.
Reconnect with `Last-Event-ID` to replay without duplicate terminal events.

Deterministic provider waits stop cooperatively. Azure OpenAI and Ollama check
before and after their transport call, but an already active transport may not
be interruptible. In that case AgentBus reports the completed-after-request
operation truthfully and waits for a safe checkpoint.

On restart, persisted cancellation is reloaded. Stale process-local operation
markers are abandoned without being reported as completed, stale leases remain
subject to fencing, and terminal successful tasks are not rerun.

## True offline acceptance

Run:

```powershell
.venv\Scripts\python.exe -m agentbus.control.acceptance
```

The acceptance launches a loopback daemon with a credential-stripped child
environment and creates temporary Git repositories. It exercises a complete
deterministic durable run, managed write and audit, exact tool approval and
resume, traversal and credential denial, process timeout, process-tree
cancellation, bounded output and budget failure, local MCP discovery and
invocation, provider cancellation, SSE replay, reports, changes, and diffs. It
verifies every started MCP fixture session is stopped and makes no external
network or paid provider call.
