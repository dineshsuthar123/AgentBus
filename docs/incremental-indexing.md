# Incremental Indexing

AgentBus publishes immutable repository-index snapshots into a local SQLite
database. A snapshot identifies its repository and workspace, parser versions,
project-map hash, source fingerprint, graph hash, completion state, counts, and
bounded diagnostics. Readers use only a fully published snapshot; an interrupted
operation cannot expose a half-written graph as current.

## Build and update

`agentbus index build` discovers the contained workspace, discovers projects,
extracts ownership and architecture evidence, parses supported files, resolves
references, builds dependency edges, and atomically publishes a snapshot.

`agentbus index update` compares the current inventory with the latest
compatible snapshot. Unchanged files are reused when all of these remain
compatible:

- repository and workspace identity;
- parser versions;
- project-map and relevant configuration fingerprints;
- contained relative path and content hash.

Changed and added files are reparsed. Deleted and renamed paths invalidate their
owned modules, symbols, references, edges, ownership, and dependent resolution.
Cross-file resolution and architecture summaries are rebuilt from the bounded
affected set. Reports distinguish indexed, reused, skipped, deleted, renamed,
and invalidated paths.

The indexer does not use timestamp or file size alone as correctness evidence.
Content hashes detect same-size edits, including `CODEOWNERS` changes.

## Freshness

Freshness rescans safe inventory metadata and compares supported source hashes,
parser versions, workspace identity, and canonical ownership rules. Protected
and generated paths do not make an index stale because they were never eligible
index inputs.

`partially_current` and `stale` are different:

- `partially_current` means the snapshot captured all safely discoverable input
  but one or more bounded parsers or discovery steps reported recoverable gaps.
- `stale` means eligible repository state changed after publication.

Queries retain the snapshot state. Impact and context results add uncertainty
when the index is stale, partial, truncated, or contains unresolved edges.

## Watching and scheduling

The optional watcher converts contained file events into a bounded, debounced
change set. It ignores unowned, protected, generated, symlinked, and out-of-root
paths. Overflow does not guess: it requests a safe rescan. The scheduler limits
parallel parser work and preserves deterministic output ordering independent of
worker completion order.

File watching is an optimization, not the source of truth. Explicit status,
verify, update, and repair always validate persisted state against the workspace.

## Leases, progress, and cancellation

Only one operation lease may mutate a repository index at a time. The lease has
an operation ID, owner identity, heartbeat, state, and cancellation request.
Concurrent builds fail with a conflict instead of racing SQLite writes.

Progress events have a bounded sequence and phase:

1. `discovery`
2. `indexing`
3. `invalidation`
4. `persistence`
5. `completed`, `paused`, or `failed`

Cancellation is cooperative. Checkpoints stop new parser work, preserve the
last published snapshot, and record terminal operation state. Cancellation does
not delete repository files or automatically remove the previous snapshot.

## Verify, repair, clear, and GC

- `index verify` validates schema, integrity, identity, and freshness without
  executing repository code.
- `index repair` recovers an interrupted operation and rebuilds when required.
- `index gc` removes superseded index snapshots according to bounded retention.
- `index clear` is the only explicit destructive index operation. It affects the
  selected local index records, not repository content.

Never commit `repository-index.sqlite3`, its WAL/SHM files, or local cache
directories. The default database lives beside AgentBus state rather than in the
repository. Failed AgentBus runs retain their filesystem edits for inspection;
index recovery does not imply workspace rollback.

See [Repository Intelligence](repository-intelligence.md) and
[Deterministic Replay](deterministic-replay.md).
