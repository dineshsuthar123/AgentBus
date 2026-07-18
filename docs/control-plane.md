# Local Control Plane

AgentBus v0.2 adds a thin FastAPI adapter over the existing orchestrator,
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
environment, creates temporary Git repositories, executes one complete
deterministic parallel durable run, retrieves SSE/report/change/diff APIs, then
cancels a second run while its deterministic coder provider is active. It makes
no external network or paid provider call.
