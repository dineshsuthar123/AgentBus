# ADR 0007: Local Control Plane And Native VS Code Integration

## Decision

Use a loopback-only FastAPI/Uvicorn daemon and a native TypeScript VS Code
extension. Reuse SQLite durability and expose a stable `/api/v1` protocol.
Stream progress with SSE because it is one-way, ordered, HTTP-native, and
supports cursor replay without a WebSocket command channel.

## Security

Use a per-daemon bearer token delivered once to the parent process and retained
by VS Code SecretStorage. Registry metadata is non-secret. Bind and Origin
policies are loopback-only. Git and file APIs preserve existing repository and
artifact boundaries.

## Consequences

Provider calls are not assumed to be forcibly interruptible. Cancellation
persists request, propagation, acknowledgement, provider, scheduling, cleanup,
and resumability facts while an in-flight operation may finish. See
[ADR 0008](0008-cooperative-cancellation-and-real-local-execution.md) for the
cooperative state machine and true local acceptance decision. Filesystem edits
remain after failure. Worktree cleanup remains explicit. Protocol generation
allows future IDE clients without coupling them to Python database rows.
