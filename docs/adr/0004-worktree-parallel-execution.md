# ADR 0004: Worktree Parallel Execution

- Status: Accepted
- Date: 2026-07-13

## Context

AgentBus durable execution already persists validated task graphs, immutable attempts, approval decisions, scoped artifacts, final verification, and final review. Sequential execution is safe but cannot overlap independent graph tasks. Running multiple coders in one working tree would create ambiguous ownership, duplicate execution races, mixed diffs, unsafe commits, and non-deterministic integration.

This milestone needs bounded local concurrency while preserving workspace-repository validation, generated-artifact filtering, `shell=False`, terminal task immutability, approval gates, final acceptance, and the user's existing checkout.

## Decision

Parallel durable execution is explicit and remains disabled by default. A foreground bounded scheduler extends the existing durable graph, task executor, StateStore, agents, tools, verifier, reviewer, Git repository abstraction, and finalization flow. It does not introduce a second workflow engine.

Each parallel run captures an exact base commit and creates:

- one AgentBus-owned integration branch and worktree;
- one AgentBus-owned branch and worktree for each active task;
- one persisted SQLite lease for each executing task;
- one scoped task commit for each successful task;
- persisted deterministic integration attempts.

## Why Git Worktrees

Git worktrees provide independent filesystem and index state while sharing the target repository's object database. They let existing workspace-scoped tools run unchanged against a canonical isolated path and let successful task output become an immutable commit before coordination.

The manager validates both the worktree Git top-level and common Git directory. Worktrees live under a configured canonical root outside the target repository. Paths and refs are generated from sanitized components plus hashes. Existing, missing, dirty, mismatched, or unowned paths fail closed.

The user's checked-out branch, index, uncommitted changes, and HEAD are not task or integration surfaces. AgentBus never automatically merges or checks out the integration result there.

## Why Sequential Remains Default

Sequential mode supports non-parallel plans and existing behavior without requiring a Git worktree lifecycle. Parallel execution adds repository, lease, commit, integration, and recovery constraints and is therefore explicit through configuration or CLI. The configured worker count is bounded and defaults to one even when parallel mode is enabled.

No background service is started. This is a local foreground scheduler, not a distributed worker platform.

## Lease And Fencing Semantics

SQLite remains durable workflow truth. Lease acquisition uses a short `BEGIN IMMEDIATE` transaction, a partial unique index for one active lease per task, and coordinated `ready -> running` activation. Heartbeat renewal verifies lease ID, worker ID, status, expiry, and fencing token.

Expired leases can be reclaimed. Each acquisition receives a monotonically increasing task-local fencing token. Task success, attempt completion, and task-commit insertion occur in one transaction that revalidates the non-expired lease and token. A stale worker may finish local computation or leave diagnostic files, but it cannot persist success after losing ownership.

This provides at-most-one valid lease holder and duplicate execution prevention under persisted local lease rules. It does not claim distributed exactly-once execution, consensus, or transactional rollback of arbitrary external side effects.

## Task Commits Before Integration

A successful task stages only run-attributed commit-eligible paths in its worktree and creates exactly one task commit. Generated and ignored artifacts remain excluded. Persisting the commit before integration gives recovery a stable identifier, prevents mixed task staging, preserves failed/conflicting work for inspection, and allows integration to be replayed idempotently.

If a process stops after creating a single task commit but before persisting it, recovery accepts it only when the owned worktree is clean and its history is exactly one commit above the persisted base. Ambiguous histories fail rather than being guessed.

## Deterministic Integration

The integration coordinator cherry-picks successful task commits in graph topological order, with stable task-ID ordering within each level. Downstream task worktrees are created only from an integration commit that already contains every successful dependency.

Each integration attempt persists its task commit and current integration base before Git mutation. Successful resulting commits are persisted before the task becomes terminally succeeded. Already integrated task commits are not reapplied during resume.

Determinism favors reproducibility and safe recovery over maximum throughput. Workers may finish out of order, but integration order does not depend on timing.

## Conflict Policy

Conflicts halt the run. AgentBus records bounded repository-relative conflict paths, aborts only the AgentBus-owned in-progress cherry-pick, verifies that the integration worktree returned to its persisted base, and retains task commits and worktrees. It never silently chooses content, invokes a model to resolve a conflict, force-checks out, resets, cleans, or modifies the user's branch.

## Final Acceptance And Publication

After all task commits integrate, full final verification and the mandatory whole-run reviewer operate on the integration worktree. Task history remains successful if later final acceptance fails. Verifier failure or reviewer rejection fails the run and prevents a user-facing branch, commit identifier, push, or PR.

When explicitly requested and accepted, AgentBus creates a user-facing branch ref at the verified integration commit without checking it out. Push and PR creation remain separate explicit options.

## Cleanup Safety

Worktrees are retained by default for diagnostics. Cleanup is an explicit CLI operation over persisted AgentBus ownership records. A worktree must validate against the expected repository and configured root, be marked `cleanup_pending`, and be clean before normal `git worktree remove` is allowed. Dirty, unknown, orphaned, or mismatched paths are refused. No force removal or automatic destructive rollback is available.

Internal refs may remain after worktree removal so task commits are not accidentally destroyed. Ref pruning is outside this milestone.

## Recovery

Resume expires stale leases transactionally while leaving valid active lease holders untouched. Unleased interrupted tasks follow the existing bounded retry policy. Persisted worktrees are revalidated, unpersisted single task commits can be reconciled, interrupted integration is either recognized as already applied or safely aborted for deterministic retry, and terminal successful tasks are never re-executed.

SQLite schema version 2 adds `worktrees`, `worker_leases`, `task_commits`, and `integration_attempts` through an explicit transactional migration from version 1. `StateStore.backup()` provides an operator-controlled database copy mechanism.

## Consequences And Future Direction

Benefits include isolated concurrent task filesystems, transactional local ownership, stale-worker fencing, deterministic integration, conflict diagnostics, and unchanged user checkouts. Costs include retained worktrees and refs, additional Git commits, SQLite migration maintenance, and conservative halt-on-ambiguity recovery.

A future milestone may add supervised multi-process workers, queue backpressure, explicit internal-ref pruning, conflict-resolution approvals, and stronger cancellation. Remote workers would require a different lease store, authenticated artifact transport, repository synchronization, and distributed failure semantics; the local SQLite protocol must not be presented as sufficient for multi-host consensus.
