# ADR 0009: Capability-Based Managed Tool Runtime

- Status: Accepted
- Date: 2026-07-22

## Context

AgentBus already had durable task graphs, approvals, cancellation, worktrees,
Git finalization, a local control plane, and provider-neutral model routing.
Filesystem, command, Git, verifier, and future external tools did not share one
versioned authorization and accounting boundary. Extending each path
independently would make workspace scope, approval semantics, retries, process
cleanup, resource limits, and audit behavior diverge.

Model output and repository code are untrusted. A tool name or a local MCP
server is not authority. AgentBus also cannot honestly claim complete sandbox
isolation without a kernel or virtual-machine boundary.

## Decision

All model-requested and verifier-managed tools use the `agentbus.tool` protocol
through one `ManagedToolRuntime` facade. Existing repository, filesystem, Git,
process, state-store, approval, cancellation, event, and worktree components
remain authoritative behind adapters; the runtime does not replace the durable
orchestrator.

The invocation path is:

1. Resolve a deterministic versioned descriptor from `ToolRegistry`.
2. Validate bounded JSON arguments against the local descriptor schema.
3. Independently derive exact capabilities and resource scopes.
4. Reject a model or planner capability mismatch.
5. Evaluate deterministic policy against caller, workspace, worktree,
   Workspace Trust, provider consent, paths, executable, network, and budget.
6. Bind risky work to an exact persisted approval request.
7. Reserve cumulative run and task budget.
8. Dispatch through a contained adapter with cancellation.
9. Persist the terminal result and immutable audit record.

Protocol records are frozen and versioned. Invocation identity includes run,
task, tool, protocol, arguments, capabilities, canonical workspace/worktree,
budget, cancellation revision, invocation revision, and idempotency identity.
Reusing an ID with changed identity fails closed.

## Capability and policy model

Capabilities are named, explicit, and scoped. There is no production `all` or
unrestricted wildcard. Tool descriptors declare their maximum requirements,
while concrete invocation scopes are derived from validated arguments.

Policy has four outcomes: allow, allow with exact constraints, require exact
approval, and deny. Approval cannot override a deny. A grant is bound to the
complete request, including capability and argument hashes, worktree, resource
budget, protocol and tool versions, cancellation revision, and invocation
revision. All checks run again after approval.

Reviewers are read-only by default. Mutation and process execution require a
trusted workspace. Network remains disabled unless an explicit run policy
enables it, after which destination-scoped approval is still required.

## Process and filesystem boundary

Managed processes use an absolute revalidated executable identity, separate
arguments, `shell=False`, a contained working directory, a minimal environment,
bounded output, timeout, cancellation, and process-tree cleanup. Windows uses
Job Objects with a `taskkill` fallback. POSIX uses process groups and bounded
signal escalation. Unsupported memory, CPU, or child limits are reported as
unsupported rather than claimed as enforced.

Filesystem adapters canonicalize roots and reject traversal, symlink/junction
mutation, device paths, alternate data streams, UNC paths, protected secrets,
and changed root identity. Mutations are hash-aware, bounded, attributed, and
atomic where the platform permits.

Managed Git adapters validate exact repository top-level equality, use explicit
arguments and path separators, disable hooks, bound and redact output, and
permit mutation only in AgentBus-owned worktrees. Remote and destructive Git
operations are absent from the managed registry.

## MCP decision

MCP client support is explicit and local only: supervised stdio or explicitly
authenticated numeric-loopback HTTP. Every imported tool has a predeclared
capability map, a namespaced registry identity, local schema validation, normal
policy evaluation, and exact approval. Sessions are bounded and closed with the
owning runtime.

AgentBus exposes a separate authenticated MCP endpoint with a fixed set of
bounded control operations. It does not expose arbitrary files, process
execution, raw SQLite, secrets, approval decisions, commit, push, PR creation,
or live-provider submission.

## Persistence and recovery

SQLite remains the source of truth. Invocation, policy, approval, budget,
result, cancellation, and audit records are persisted. Startup reconciles
stale running invocations before restoring cumulative accounting. No daemon
attempts to adopt an unknown process from a previous instance.

Terminal successful tasks and attempts remain immutable during durable resume.
A later final-review rejection fails the overall run without rewriting prior
successful task history.

## Consequences

Benefits:

- one authorization, approval, budget, cancellation, and audit contract;
- consistent workspace/worktree propagation across sequential and parallel
  execution;
- deterministic offline testing for safe, denied, approval, timeout,
  cancellation, budget, Git, and MCP paths;
- safe control-plane and VS Code inspection without raw secrets;
- explicit extension points without model-controlled imports.

Costs and limitations:

- descriptors and capability maps require deliberate maintenance;
- conservative defaults reject some legitimate workflows until a safe adapter
  or explicit policy is added;
- local interpreters and MCP peers still run with the AgentBus OS account;
- policy network denial is not an OS firewall;
- POSIX process resource enforcement is incomplete;
- external and filesystem side effects are not transactionally rolled back;
- this design is defense in depth, not malicious-code containment.

## Rejected alternatives

- Keep direct per-agent tools: rejected because policy and recovery invariants
  would diverge.
- Trust model-declared capabilities: rejected because model output is not an
  authorization source.
- Treat local MCP servers as trusted plugins: rejected because locality and a
  server name do not constrain behavior.
- Expose arbitrary commands through the control plane: rejected because it
  bypasses Workspace Trust, policy, approval, and executable validation.
- Automatically reset or delete files after failure: rejected because it can
  destroy user-owned changes and cannot reverse external effects.
- Claim complete sandboxing: rejected because the implementation does not
  provide a kernel or virtual-machine isolation boundary.
