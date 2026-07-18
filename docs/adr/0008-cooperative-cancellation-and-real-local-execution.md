# ADR 0008: Cooperative Cancellation And Real Local Execution

## Status

Accepted.

## Context

The local control plane previously accepted cancellation but could not describe
whether an active provider observed it, whether a non-interruptible operation
finished, or whether scheduling and cleanup had stopped safely. The offline
acceptance and VS Code Electron tests also used seeded state or command
registration as substitutes for real execution.

Python `Future.cancel()` does not stop an active thread. Azure OpenAI and Ollama
requests may be inside transports that AgentBus cannot safely interrupt. Git
commits, verifier commands, and persistence checkpoints must not be abandoned
halfway through. Any stronger cancellation claim would be misleading.

## Decision

Use one thread-safe cooperative cancellation token per run. Persist its
monotonic revisions, request and acknowledgement facts, provider signalling,
active operations, completed-after-request operations, scheduling stop,
cleanup, task effects, resumability, and terminal reason in SQLite.

Every runtime boundary receives the same token. Interruptible deterministic
provider waits acknowledge immediately. Azure and Ollama check before and after
transport work and truthfully report when a response completed after a request.
Non-interruptible critical sections finish, persist their outcome, and are
reported rather than forcefully terminated.

After cancellation, schedulers create no new lease or worktree. Workers release
leases and do not retry cancellation. Successful task attempts and task commits
remain immutable. Final review remains mandatory before a user-facing commit or
PR, and cancellation or rejection prevents those side effects.

Protocol v1 gains defaulted cancellation fields, preserving old clients.
Lifecycle events use persisted SQLite event IDs and are replayable over SSE.
Process recovery clears stale in-memory operation markers without claiming an
operation completed. Terminal successful tasks are never rerun.

Offline acceptance now launches the real daemon and executes planner, durable
graph, scheduler, worker, deterministic provider, tools, verifier, task review,
task commit, integration, final verification and review, events, report, and
commit-backed diff. VS Code Electron repeats this path, cancels a second active
provider request, opens native diff and report documents, restarts the daemon,
and reloads persisted runs.

## Consequences

Cancellation latency is bounded by cooperative checkpoints and any current
non-interruptible operation. The API distinguishes request, provider signal,
acknowledgement, completed-after-request work, scheduling stop, cleanup, and
terminal state instead of promising forced interruption.

Filesystem edits and retained worktrees are not rolled back automatically.
Reports preserve their paths and may offer manual cleanup guidance, but
AgentBus never resets, cleans, or deletes user changes.

The deterministic provider is a production development and acceptance adapter,
not a quality substitute for a real model. Its fixed profiles deliberately
support only bounded offline scenarios.
