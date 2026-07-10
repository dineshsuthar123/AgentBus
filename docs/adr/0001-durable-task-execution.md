# ADR 0001: Durable Task Execution

- Status: Accepted
- Date: 2026-07-10

## Context

AgentBus could execute one Planner -> Coder -> Verifier -> Reviewer workflow, but run state existed primarily in process memory and JSONL audit logs. A terminal closure, malformed task response, exhausted correction, or machine restart could lose the orchestration position and force a task to start over.

The next local-first milestone needs deterministic task dependencies, persisted attempts, explicit human approval for high-risk work, and safe resume behavior. It must preserve the current agents, workspace safety controls, `shell=False` command construction, optional Git automation, and offline tests.

## Decision

AgentBus adds an opt-in durable execution package with five responsibilities:

1. Typed run, task, attempt, artifact, approval, retry, and report models.
2. A validated directed acyclic task graph derived from planner output.
3. A versioned SQLite state store that owns recovery state.
4. Explicit run, task, and attempt transition policies.
5. A sequential engine that coordinates injected task executors.

The existing multi-agent workflow is adapted as a task executor. For each ready graph task it invokes the current Coder, Verifier, Reviewer, Git diff reader, and safety-controlled tools. The persistence layer never calls a model or tool.

Regular non-durable execution remains the default.

## Why SQLite

SQLite fits this local-first checkpoint because it is included with Python, supports transactions and foreign keys, has no service dependency, and can be reopened by a new AgentBus process. The store uses parameterized SQL, short-lived connections, foreign-key enforcement, write-ahead logging, a busy timeout, and immediate write transactions. Status validation and its event are committed together where practical.

The schema has an explicit version row. A database newer than the running code, or an older database without a registered migration, fails with a domain error instead of being modified speculatively.

SQLite is not being presented as a distributed queue. This milestone expects one active sequential runner per run. Transactional transitions prevent two contenders from both moving the same `ready` task to `running`, but no long-lived distributed lease or remote worker ownership protocol exists.

## Why JSONL Is Not Recovery State

JSONL remains a useful append-only audit and troubleshooting output. It is intentionally not the recovery source because rebuilding current state from partial event files would require replay ordering, idempotency, schema evolution, and corruption handling that the previous logger did not provide.

SQLite stores the current records and compact events needed for recovery. JSONL mirrors machine-readable lifecycle events. Both outputs redact secret-shaped fields and bound text; neither stores a full environment dump.

## State Invariants

The transition policy enforces these invariants:

- Terminal tasks cannot transition back to ready or running.
- A task becomes ready only after all required dependencies succeed.
- Failed, rejected, blocked, or cancelled dependencies propagate blocked state.
- An attempt row exists before task executor invocation.
- Every retry is a new attempt with a monotonically increasing task-local number.
- Run, task, and attempt state changes reject invalid lifecycle edges.
- A high-risk task cannot enter running state without a persisted explicit approval.
- A run succeeds only when every graph task succeeds.
- Failed or rejected durable runs cannot reach Git commit or PR finalization.
- PR creation is opt-in and requires a persisted successful commit.

Planner dependencies are optional. When every step omits the field, task order becomes a sequential chain for compatibility. Duplicate IDs, missing dependencies, self-dependencies, and cycles are rejected before the run and tasks are persisted.

## Crash Recovery Policy

Resume reads a consistent `RunSnapshot` from SQLite and never reconstructs state from JSONL.

For a task persisted as `running`:

- A latest `running` attempt is completed as `interrupted`.
- A latest `succeeded` attempt is reconciled by promoting the task to `succeeded`.
- A failed or interrupted attempt is moved through `retryable` to `ready` only if its failure category and remaining budget permit another attempt.
- An exhausted task becomes `failed`, and failed dependency state propagates to downstream tasks.

Already succeeded terminal tasks are never selected again. Attempt numbering is calculated from persisted rows, so process recreation does not reset it.

This policy does not claim exactly-once execution for arbitrary side effects. If a command or file write succeeds but the process stops before success is persisted, the attempt is considered interrupted and may run again. Future workspace isolation can make this safer; current task executors should be restart-tolerant.

## Approval Policy

Low-risk tasks proceed normally. Medium-risk tasks are identified in structured ready events. High-risk tasks move to `waiting_for_approval`, and the run pauses before executor invocation.

Only explicit CLI approval writes an approved decision. Reviewer or planner model output cannot approve high-risk execution. Rejection is persisted, marks the task rejected, blocks dependent tasks, and prevents Git finalization.

## Why Sequential First

Sequential execution minimizes ambiguous ownership and merge behavior while the persistence and recovery contracts mature. Deterministic graph order makes failures reproducible and lets the engine prove that only one ready task is selected at a time.

The graph and store do not encode sequential-only dependency rules. A future scheduler can claim multiple independent ready tasks transactionally after adding worker leases and workspace isolation.

## Consequences

Positive consequences:

- Runs survive process and `StateStore` recreation.
- Task attempts, decisions, and artifacts are independently inspectable.
- Existing agents and safety controls remain the implementation path.
- Unit and integration tests use injected offline executors.
- Git and PR side effects remain downstream of persisted success.

Costs and limitations:

- SQLite adds schema and transition maintenance responsibilities.
- The CLI remains a foreground runner with no hidden worker.
- Tasks share one target workspace.
- Partial external side effects cannot be rolled back automatically.
- A future schema change must add an explicit migration.

## Future Direction

The recommended next milestone is isolated bounded parallel execution:

1. Add per-task Git worktrees or equivalent workspace snapshots.
2. Add transactional worker leases with expiry and heartbeat metadata.
3. Claim only independent ready tasks up to a configured concurrency bound.
4. Merge successful task branches in deterministic graph order.
5. Detect and surface merge conflicts as durable task outcomes.
6. Add explicit SQLite migrations and database backup/repair tooling.

Parallel execution should not be enabled until lease recovery and workspace merge semantics preserve the invariants in this decision.
