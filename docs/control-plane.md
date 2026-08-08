# Local Control Plane

AgentBus v0.4 keeps a thin FastAPI adapter over the existing orchestrator,
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

## Trace and replay

Bounded trace inspection is available at:

- `GET /api/v1/runs/{run_id}/trace`
- `GET /api/v1/runs/{run_id}/trace/spans`
- `GET /api/v1/runs/{run_id}/trace/spans/{span_id}`
- `GET /api/v1/runs/{run_id}/provenance`
- `GET /api/v1/runs/{run_id}/replayability`

Span and replayability lists use bounded sequence pagination. Responses expose
safe identities, hashes, counts, classifications, and diagnostic summaries.
They do not expose unrestricted trace blobs, raw SQLite, prompts, credentials,
provider payload values, or private replay paths.

Managed providerless replay and comparison routes are:

- `POST /api/v1/runs/{run_id}/replays`
- `GET /api/v1/replays`
- `GET /api/v1/replays/{replay_id}`
- `POST /api/v1/replays/{replay_id}/cancel`
- `POST /api/v1/comparisons`
- `GET /api/v1/comparisons/{comparison_id}`

Replay creation requires an explicit mode. Offline replay sends no live
provider consent and records provider and network call counts. Checkpoint
replay exposes only the stable
`daemon_managed_temporary_workspace` isolation label. Fork replay persists a
new trace and provenance manifest plus an automatic structured comparison.
Terminal sessions and comparisons survive daemon restart.

Archive and fixture routes are:

- `GET /api/v1/traces/{trace_id}/export`
- `POST /api/v1/traces/import`
- `POST /api/v1/runs/{run_id}/fixtures`

The HTTP transport validates canonical base64, SHA-256, identity, and a
650,000-byte decoded limit. Source-like export, import, and fixture capture
require explicit consent. Import and capture always return
`replay_started: false`; execution is a separate action.

See [Execution Tracing](execution-tracing.md),
[Deterministic Replay](deterministic-replay.md), and
[Trace Archives](trace-archives.md).

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

## Repository intelligence

Control protocol v1 exposes additive authenticated routes for local repository
intelligence:

- `POST /api/v1/workspaces/index` and `/index/attach`;
- `GET /api/v1/workspaces/{workspace_id}/index`;
- `POST` update, verify, repair, and cancel operations under that index;
- bounded search and symbol detail;
- bounded dependencies and dependents;
- impact analysis and relevant-test selection;
- role-specific context-plan previews.

Build, update, and repair require an explicit trusted-workspace assertion. The
daemon canonicalizes and contains the workspace, binds it to a stable workspace
identity, and keeps the index database beside daemon state. Unknown workspace
IDs fail closed. Graph depth, result pages, queries, subjects, diagnostics, and
progress events are schema bounded.

Responses include paths, symbol metadata, hashes, confidence, uncertainty,
state, and safe explanations. They omit raw source, prompts, secrets, API keys,
bearer tokens, personal absolute paths, and embedding vectors. All normal
repository-intelligence responses report zero provider and network calls.

See [Repository Intelligence](repository-intelligence.md) and the generated
[Control Protocol v1](protocol-v1.md).

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
invocation, provider cancellation, SSE replay, reports, changes, and diffs.
It also verifies hierarchical traces, provenance, full and checkpoint replay,
fork comparison, blob tamper detection, archive export and isolated import,
fixture capture and execution, cooperative replay cancellation, and
provider/network call counts. It also builds and incrementally updates a real
mixed-language index, checks protected-path exclusion, executes an
intelligence-guided run, detects replayed index drift, exercises index
cancellation, and repairs after daemon restart. It verifies every started MCP
fixture session is stopped and makes no external network or paid provider call.
