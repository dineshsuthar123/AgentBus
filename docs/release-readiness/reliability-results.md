# Reliability, interruption and soak evidence

Evidence captured 2026-11-18T08:24:39.122835+00:00. Commit `3a36800fde7d213af5f742da816574612a3e78cd` plus the preserved dirty tree on
`feature/release-hardening-20261118`; this is **not a committed release candidate**.
Source digest: `45c66d3cf94db7aaf5b98a72b73ee141b1ea5263ee14f13a4a2fe19fd2c58996` (per-file inventory and dependency
versions: `.tmp/release-readiness/20261118/candidate-complete.json`). Native Windows 11
build 26200, AMD64, 16 logical processors, 15.3 GiB RAM; Python 3.12.10,
Node 22.17.0, npm 10.9.2, FastAPI 0.163.2, Pydantic 2.14.3, pytest 9.2.0.
Vite 8.3.8 and VS Code 1.96.4 were used. Command timestamps are UTC with full
dates in metadata and tables. The main validation campaign ran on 2026-11-17 UTC;
report finalization continued on 2026-11-18 UTC (2026-11-18 in Asia/Calcutta).
The digest covers tracked files plus scoped new implementation, tests, config and
product docs; generated Studio assets and these self-referential audit reports
are excluded. The Studio manifest and artifact hashes cover those generated assets.
Per-interpreter dependency/origin inventories and browser versions are in
`.tmp/release-readiness/20261118/environment-inventory.json` (Edge 157.0.4341.60).

[Evidence ledger](evidence-ledger.md) records commands, start/end times,
exit status and log SHA-256 references. Each prefix refers to ignored local
`<prefix>.json` metadata and `<prefix>.log` under `.tmp/release-readiness/20261118/`.
Metadata preserves exact invocations; repository-specific API targets are
abbreviated in the public command ledger.
`candidate-resumed.json` confirms the source, branch and HEAD were unchanged
after the interruption. Dependency audits were refreshed on 2026-11-18 UTC.
The only subsequent source edit removed one trailing blank line from
`tests/test_product_config.py`; runtime code is unchanged and the sdist was rebuilt.
The ledger includes failed runs; later passes do not erase them. Overlapping tests
are never added together as independent coverage. No paid inference, production
customer mutation, publication or deployment was performed.

The reliability campaign passed 15 scenarios over four generated repositories,
including10 cursor restart recoveries,10 offline replays and 5 intentional
cancellations. SQLite/index integrity passed, with zero reported owned process
or worktree leaks. p95 scenario latency4872.4ms, maximum5375ms; traced peak
15,286,791B and growth2,389,223B within its144,703,488B budget.

The 10-minute release-candidate soak completed227 cycles:114 successes and 113
intentional cancellations, zero unexpected failures. It performed227 approvals,
MCP calls, replays, reconnect/restart simulations, index updates and worktree
cleanups;681 managed tool calls;7,492 events with zero event gaps. No failed
cleanup, stale lease, tracked synthetic process leak or owned worktree leak was
reported. The requested10,000 is an upper limit; it was **not** the completed count.

Traced allocation peak14,962,671B; allocation growth2,833,038B. Threads1→1,
peak7. Handles177→195, peak286: an18-handle delta was observed, not silently
reported as zero. State SQLite grew356,352→12,050,432B; index SQLite
1,376,256→259,620,864B; trace1,456→117,116B. Retained history/version storage
accounts for disk growth and requires retention planning; this is not RSS.

## Extended duration

One-hour configured profile: ok=False; duration=3613.344s; completed=966; actual run outcomes `{'failed': 1, 'intentional_cancellations': 482, 'successful': 483}`.

Operations: `{'approvals': 965, 'daemon_reconnects': 965, 'daemon_restarts': 965, 'index_updates': 965, 'mcp_calls': 965, 'replays': 965, 'tool_calls': 2895, 'trace_writes': 965, 'worktree_cleanups': 965}`.

Resources: `{"event_count": 31858, "event_gap_count": 0, "failed_cleanup_count": 0, "integrity": {"repository_index": true, "state_database": true}, "leaked_process_count": 0, "leaked_worktree_count": 0, "memory_budget_bytes": 1147142144, "memory_growth_bytes": 3433183, "peak_memory_bytes": 17568751, "process_tracking": "synchronous_children_only", "sqlite_bytes_after": 1155837952, "sqlite_bytes_before": 1740800, "stale_lease_count": 0, "trends": {"handle_count": {"after": 183, "before": 177, "delta": 6, "measurable": true, "peak": 319, "scope": "current Syndra process", "unit": "handles"}, "index_database_bytes": {"after": 1105649664, "before": 1384448, "delta": 1104265216, "measurable": true, "peak": 1105682432, "scope": "repository index database files", "unit": "bytes"}, "memory_bytes": {"after": 3435264, "before": 2081, "delta": 3433183, "measurable": true, "peak": 17568751, "scope": "Python allocations measured by tracemalloc", "unit": "bytes"}, "owned_worktree_count": {"after": 0, "before": 0, "delta": 0, "measurable": true, "peak": 1, "scope": "non-removed Syndra-owned worktrees", "unit": "worktrees"}, "process_count": {"after": 0, "before": 0, "delta": 0, "measurable": true, "peak": 2, "scope": "Syndra-owned synthetic child processes", "unit": "processes"}, "state_database_bytes": {"after": 50188288, "before": 356352, "delta": 49831936, "measurable": true, "peak": 50188288, "scope": "durable state database files", "unit": "bytes"}, "thread_count": {"after": 1, "before": 1, "delta": 0, "measurable": true, "peak": 7, "scope": "current Syndra process", "unit": "threads"}, "trace_bytes": {"after": 493498, "before": 1456, "delta": 492042, "measurable": true, "peak": 493498, "scope": "content-addressed trace storage", "unit": "bytes"}}}`.


The one-hour command exited1: one managed filesystem write failed at cycle 7.
Actual totals966 cycles,483 successes,482 intentional cancellations,1 unexpected
failure. Operations beyond the failed write completed for 965 cycles. The report
contains only a generic status and its temporary repository/state was discarded;
the underlying error category cannot be reconstructed honestly. This remains an
OPEN reliability blocker. A separate50-cycle seed 2026/parallelism 2 repeat added
observation-only capture of failed-write error category/code without changing
dispatch:25 successes,25 intentional cancellations,0 failures and no write-error
observations. It did not reproduce or clear the one-hour failure.

Soak restart/reconnect counters exercise controlled state owners, not thousands
of real browser/daemon process launches. The profile explicitly reports
`synchronous_children_only` tracking. Real forced-process restart is separately
proved by four installed-daemon scenarios: recover unchanged pre-tool task,
deny drift, preserve exhausted-budget completed work, preserve exact approval.
Approved deletion produces one effect; repeated terminal resume leaves attempt
and invocation records unchanged. Unknown/partial tool effects remain fail-closed.

The extended profile and API probe measure different processes and memory scopes;
their counters must not be combined. The defined local profiles are not a
multi-day production leak certification. No external customer system was faulted.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `reliability` | 2026-11-17T19:51:36 | 2026-11-17T19:52:03 | 0 | 26.719 |
| `soak-quick` | 2026-11-17T19:52:03 | 2026-11-17T19:52:27 | 0 | 23.391 |
| `soak-release` | 2026-11-17T19:52:27 | 2026-11-17T20:02:33 | 0 | 606.485 |
| `soak-extended` | 2026-11-17T20:03:26 | 2026-11-17T21:03:44 | 1 | 3617.891 |
| `soak-write-diagnostic` | 2026-11-17T21:14:56 | 2026-11-17T21:17:01 | 0 | 125.797 |
| `process-recovery-regression` | 2026-11-17T20:46:16 | 2026-11-17T20:48:49 | 0 | 152.078 |
| `installed-process-restart` | 2026-11-17T20:49:49 | 2026-11-17T20:51:37 | 0 | 107.672 |
