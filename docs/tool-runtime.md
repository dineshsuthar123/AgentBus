# Managed Tool Runtime

AgentBus v0.3 routes model-requested tools through one managed runtime. The
runtime is a defense-in-depth boundary around local tools, not a kernel-grade
sandbox and not a guarantee that generated code is harmless.

## Invocation flow

Every managed call follows the same sequence:

1. The model names a registered tool, supplies bounded JSON arguments, and
   declares the capability names it expects.
2. AgentBus resolves the versioned descriptor and validates the argument
   schema locally.
3. The runtime independently derives the exact capabilities and scopes from
   the descriptor, arguments, workspace, and assigned worktree. A model claim
   that differs from this derivation is rejected.
4. The policy engine returns `allow`, `deny`, `require_approval`, or
   `allow_with_constraints` with a stable rule ID and safe explanation.
5. Risky calls suspend on a persisted approval bound to the exact invocation.
6. The budget ledger reserves cumulative capacity before dispatch.
7. The adapter performs the filesystem, Git, process, test, repository, or MCP
   operation through its constrained implementation.
8. Bounded output, resource usage, artifacts, policy facts, and cancellation
   state are persisted as an immutable audit record.
9. The model receives a bounded, redacted observation rather than a raw
   subprocess handle, environment, prompt, or unrestricted file snapshot.

The protocol name is `agentbus.tool`, and the only supported protocol version
is `1.0`. Protocol models are frozen, reject unknown fields, and bound strings,
collections, schemas, arguments, structured output, and diagnostics. A
protocol-version mismatch fails before dispatch.

## Registry

`ToolRegistry` provides deterministic, version-aware lookup. Registrations
declare argument and output schemas, capabilities, safety classification,
idempotency, and cancellation support. Implementations are initialized lazily,
must present the same descriptor as their registration, and cannot replace a
duplicate name. There is no import-path, `eval`, or model-controlled plugin
loader.

Built-in tools are:

- `repository.scan`
- `filesystem.read`, `filesystem.stat`, and `filesystem.list`
- `filesystem.create`, `filesystem.write`, `filesystem.patch`,
  `filesystem.rename`, and `filesystem.delete`
- `git.status`, `git.diff`, `git.show`, `git.log`, and `git.branches`
- `git.stage` and `git.commit`
- `test.execute` and `process.execute`

Explicitly configured MCP tools are imported as
`mcp.<server-id>.<tool-name>`. They use the same registry, policy, approval,
budget, cancellation, persistence, and audit path as built-ins. See
[MCP Integration](mcp-integration.md).

## Invocation identity

A `ToolInvocation` includes:

- invocation, run, and task IDs;
- tool and tool-protocol versions;
- bounded arguments and independently derived capabilities;
- canonical workspace and worktree identities;
- caller role, Workspace Trust, and provider consent;
- timeout and resource budget;
- cancellation and invocation revisions;
- policy context and an optional idempotency key.

The invocation fingerprint covers the identity and immutable request. Reusing
an invocation ID with different arguments, capabilities, revisions, scope,
budget, or idempotency data fails closed. Duplicate calls cannot reset
cumulative accounting.

## Lifecycle and persistence

Invocation states are `requested`, `awaiting_approval`, `running`,
`succeeded`, `failed`, `denied`, `cancelled`, and `timed_out`. SQLite stores the
request record, policy decision, approval reference, result, budget usage, and
append-only audit entry. Events contain safe identifiers and summaries, not
raw arguments, prompts, inherited environments, bearer tokens, provider keys,
or unrestricted output.

At restart, AgentBus restores persisted budget reservations and reconciles
non-terminal invocations. A process from a previous daemon cannot be adopted
safely, so the stale record receives a terminal restart-cancelled or
restart-interrupted result and active accounting is abandoned without
pretending the external operation was rolled back.
Terminal task and invocation history remains immutable, and successful
terminal tasks are not rerun during durable resume.

## Default resource budget

The default `ToolResourceBudget` is independent from model token limits:

| Limit | Default |
| --- | ---: |
| Wall clock per invocation | 90 seconds |
| Retained stdout | 64 KiB |
| Retained stderr | 64 KiB |
| Combined retained output | 128 KiB |
| Artifact bytes | 5 MiB |
| Child processes | 8 |
| Concurrent processes per run | 2 |
| Invocations per task | 64 |
| Invocations per run | 512 |
| File mutations per run | 100 |
| Total written bytes per run | 10 MiB |
| Maximum individual file | 2 MiB |
| Memory | unset |
| CPU time | unset |

Invocation, process-slot, mutation, and written-byte limits accumulate across
retries and duplicate requests. Limits may tighten during a run but never
expand existing reservations. Output readers continue draining a child after
the retained limit so a full pipe cannot deadlock the process.

Every reported process limit includes `requested`, `supported`, `enforced`,
and `observed` fields. AgentBus reports unsupported memory, CPU, or child
limits as unsupported instead of treating measurement as enforcement. Platform
details are in [Sandbox Security](sandbox-security.md).

## Cancellation

Cancellation is cooperative until a managed process is active. Before launch,
the token checkpoint prevents execution. During execution, the supervisor
terminates the managed process tree and reports whether a signal was sent,
acknowledged, the process was terminated, the operation completed after the
request, and cleanup completed. Tool cancellation also advances the persisted
cancellation revision, preventing stale approval or invocation replay.

Cancelling a run stops new scheduling but does not automatically undo completed
filesystem, Git, MCP, network, or process side effects.

## Reports and retained side effects

Run reports expose tool totals, status counts, budget usage, MCP usage, safe
policy explanations, approval state, artifacts, and created or modified files.
Output and source diffs are bounded and secret-shaped values are redacted.

Failed, rejected, cancelled, and timed-out runs do not reset, clean, delete, or
roll back workspace files. Reported files remain available for inspection.
Cleanup is a separate explicit workflow and must validate AgentBus ownership;
the runtime never performs destructive rollback automatically.
