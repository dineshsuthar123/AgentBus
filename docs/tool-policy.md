# Tool Capability Policy

AgentBus authorizes each managed invocation from explicit capabilities and
concrete resource scopes. Model output proposes a tool call; it does not grant
authority. The runtime derives the required capabilities independently and the
policy engine evaluates the frozen invocation before any implementation runs.

## Capabilities

The protocol defines these capability names:

- `filesystem.read`, `filesystem.write`, `filesystem.create`,
  `filesystem.delete`, and `filesystem.rename`
- `process.execute` and `process.network`
- `git.read`, `git.write`, `git.commit`, `git.branch`, and `git.worktree`
- `test.execute` and `package.install`
- `environment.read_safe`
- `mcp.connect` and `mcp.invoke`

A `CapabilityScope` can constrain canonical roots, path patterns, affected
paths, executable aliases, working directories, network permission and
destinations, environment keys, Git operations, and MCP server IDs. Scope
entries are bounded, unique, NUL-free, and reject production wildcards such as
`*`, `**`, `all`, and `unrestricted`.

Descriptors declare the maximum capability shape. The runtime derives an exact
invocation scope from validated arguments. It rejects missing, additional, or
changed model capability claims before policy evaluation. Planner-declared
capabilities are another upper bound and cannot be expanded by the coder.

## Policy outcomes

Every decision includes an outcome, stable rule ID, safe reason, invocation
revision, capability fingerprint, argument hash, optional constraints, and
bounded metadata.

| Outcome | Meaning |
| --- | --- |
| `allow` | A bounded read-only operation may proceed. |
| `allow_with_constraints` | The exact derived scopes remain mandatory during execution. |
| `require_approval` | Execution suspends until the exact persisted request is decided. |
| `deny` | The invocation is terminally refused and approval cannot override it. |

Policy evaluation is deterministic for the same descriptor, invocation, and
configuration. Decisions and approval transitions are persisted for replay and
safe UI presentation.

## Secure defaults

The default policy allows:

- bounded ordinary-file reads inside the assigned roots;
- repository scans and read-only Git status, diff, show, log, and branch
  inspection;
- scoped writes and creates in a trusted worktree when provider execution has
  been consented and the descriptor is not classified risky or dangerous;
- configured tests and process execution through the controlled supervisor
  when the executable is standard, the worktree is trusted, network is off,
  and the automatic process budget is not exceeded.

Standard executable aliases are `python`, `python3`, `pytest`, `git`, `node`,
and `npm`. The executable catalog still resolves each alias to an absolute
identity and revalidates it at launch; an allowlist name alone is not trusted.

The default policy requires approval for:

- every configured MCP invocation;
- deletion;
- package installation;
- more than 32 affected paths;
- CI paths under `.github/workflows/` or `.gitlab-ci`;
- `deploy/`, `deployment/`, `infra/`, and `security/` paths;
- a nonstandard executable;
- a process budget above the 300-second automatic policy threshold;
- explicitly enabled network execution, scoped to its destination;
- risky operations not covered by a narrower automatic rule.

The default policy denies:

- traversal, NUL, UNC, Windows device, or alternate-data-stream path syntax;
- paths outside the canonical workspace or assigned worktree;
- protected credentials, private keys, daemon registries, or control-state
  paths;
- shell interpreters and `shell=True`;
- destructive, remote, or global Git operations;
- network use unless the run policy explicitly enables it;
- mutations or process execution from the reviewer role;
- mutations or process execution in an untrusted VS Code workspace.

Approval never converts a deny into an allow. The normal path, executable,
filesystem, Git, protocol, budget, and runtime checks execute again after an
approval is accepted.

## Exact approval binding

A tool approval request records:

- approval, invocation, run, and task IDs;
- invocation revision, tool version, and tool-protocol version;
- requested capabilities and their fingerprint;
- canonical workspace and worktree identities;
- argument hash and redacted argument summary;
- affected paths, executable, working directory, and network destination;
- the complete resource budget and cancellation revision;
- a hash of the idempotency key;
- policy rule, reason, proposed constraints, and optional expiry.

The persisted grant contains a hash of the complete request. Before dispatch,
AgentBus compares every binding field with the current invocation. A grant is
invalid if arguments, capabilities, scope, task, run, executable, worktree,
budget, cancellation revision, idempotency identity, tool version, protocol
version, or invocation revision changed. It cannot be reused for another task,
another worktree, a broader path set, or a later request revision.

Approvals are immutable decisions. Repeated submission of the same decision is
idempotent; a conflicting disposition or revision is rejected. Rejection keeps
the invocation terminal and prevents dependent durable work and Git
finalization where applicable.

## Workspace and role context

The workspace and worktree are canonical absolute identities, not display
labels. In parallel execution, tools receive the assigned task worktree while
the source checkout remains outside the execution surface. A reviewer receives
read-only access by default. VS Code mutations and process calls require
Workspace Trust, and multi-root workspaces require an explicit repository
selection.

Provider consent and Workspace Trust are policy inputs, not substitutes for
capabilities. Neither can bypass protected files, repository boundaries,
approval binding, or a deny rule.

## Inspecting policy

The authenticated control plane exposes:

- `GET /api/v1/policy` for bounded rule metadata;
- `POST /api/v1/policy/evaluate` for local dry-run evaluation of a registered
  tool call;
- run approval APIs for explicit approve or reject decisions;
- invocation and audit APIs containing safe persisted outcomes.

Dry-run evaluation does not execute the tool or create an approval. API
responses use hashes and bounded summaries in place of raw sensitive
arguments. The VS Code `AgentBus: Show Tool Policy` command presents the same
safe rule information.
