# ADR 0012: Local demo data and request boundaries

## Status

Accepted for the local-first Studio. This does not authorize a hosted executor,
multi-tenant service, deployment, or live model usage.

## Context

The execution runtime already has transactional SQLite state, foreign keys,
WAL, bounded writer retries, immutable terminal attempts, and provenance
records. Replacing that storage with a remote service would make the offline
demo harder to install without solving an observed local problem.

The Studio read path did have specific problems: a run list opened another
database connection for every cancellation projection; creation-ordered
history lacked matching indexes; cursors repeated search text and could exceed
their own URL limit for non-ASCII queries; synchronous service calls could
occupy the async HTTP event loop.

## Decision

Keep SQLite as the local durable source of truth. Schema version 7 adds only:

- `idx_runs_history(created_at DESC, run_id ASC)`.
- `idx_runs_status_history(status, created_at DESC, run_id ASC)`.

The migration does not rewrite runs, attempts, events, approvals, or artifacts.
The existing migration coordinator remains the supported backup, planning,
and verification path. Older clients must continue rejecting a newer schema
rather than trying to downgrade it. Use a database backup, not a filesystem
copy of an active WAL database, for recovery.

History uses creation identity as its keyset boundary. Mutable update times
cannot reorder already loaded work. New versioned cursors carry the boundary
and a digest of the filters, not an expanding copy of the search text. Existing
four-element cursors remain readable. A cursor is a pagination hint, not an
authorization credential; every request still authenticates normally.

Run lists read cancellation projections in one store connection, in batches
of at most 500 identities. A nonempty list therefore uses two store connections
instead of one plus the number of runs. Missing runs are errors, not invented
default records; existing runs without cancellation records get independent
default snapshots.

Synchronous database, filesystem, Git, verification, and tool-service endpoints
use FastAPI's worker-thread dispatch. Async request-body handling and SSE
remain async; MCP service execution is explicitly offloaded. A barrier-based
test checks that `/health` remains available during a held history read.

Invalid history filters or cursors return bounded HTTP 400 errors. A busy
database returns a redacted HTTP 503 response with `Retry-After`. The browser
can retry an idempotent read once, but must never replay a mutation implicitly.

## Browser boundary

Connection handshakes and refreshes belong to a session generation. Disconnect,
replacement, authentication failure, and unmount retire outstanding work.
Late responses cannot resurrect a session or populate another session's state.
Disposal clears in-memory authorization, query data, and pending requests.

The read cache uses recency eviction, with at most 128 identities and a 4 MiB
serialized UTF-16 size budget. Individual responses above 512 KiB are not
cached. This is a serialized-data budget, not a claim about exact JavaScript
heap usage. Invalidation also fences older in-flight results. Mutations clear
affected freshness on both dispatch and settlement and are never auto-retried.

A mutation timeout means the action was not confirmed. The interface directs
the user to inspect saved state before trying again; cancelling an HTTP request
is not an assertion that its server-side work was rolled back.

## Consequences

WAL remains a same-machine, single-writer design, not distributed database
infrastructure. The indexes cost additional write work but directly match the
history ordering and status filter. Substring search still scans qualifying
records; these indexes do not turn `instr` into full-text search. Add FTS or a
server database only when measured workload and a separately reviewed hosting
architecture justify them.

The ledger distinguishes recent counts from complete history and preserves a
snapshot's original freshness across additional pages. Missing checks and
review outcomes are displayed as unreported, never inferred successes.

## References

- [SQLite WAL behavior and limits](https://www.sqlite.org/wal.html).
- [SQLite multi-column indexes and query planning](https://www.sqlite.org/queryplanner.html).
- [FastAPI synchronous and asynchronous path operations](https://fastapi.tiangolo.com/async/).
