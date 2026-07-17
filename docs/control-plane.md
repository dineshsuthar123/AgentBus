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
