# Managed tools and approvals

Models do not execute arbitrary shell text. AgentBus validates structured tool
calls against a versioned registry, independently derives capabilities, applies
deterministic policy, reserves bounded resources, dispatches an adapter, and
records a sanitized immutable audit entry.

Read-only, workspace-contained operations may be allowed directly. Scoped
writes and tests run with constraints. Deletes and other high-risk operations
require an exact, revision-bound approval.

```console
agentbus show-run <run-id>
agentbus approve <run-id>:<task-id> --reason "Reviewed exact deletion scope"
agentbus resume <run-id>
```

Approval covers only the recorded run, task, tool version, canonical arguments,
capabilities, workspace, budget, and cancellation revision. Any material change
invalidates it. Rejection prevents the operation:

```console
agentbus reject <run-id>:<task-id> --reason "Scope is broader than intended"
```

Built-in subprocesses use an absolute executable identity, separate arguments,
`shell=False`, a sanitized environment, bounded output, timeouts, and process
tree cleanup. These controls are defense in depth, not a kernel sandbox.

Read [Managed Tool Runtime](../tool-runtime.md),
[Tool Capability Policy](../tool-policy.md), and
[Sandbox Security](../sandbox-security.md).
