# Measured performance and limits

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

Both 250-file, five-sample broad benchmark runs passed all 11 configured latency
and allocation budgets, with the same environment and repository fingerprints.
These are two successive candidate measurements, not a causal before/after
optimization experiment. Concurrent validation and Windows scheduling add noise.
Small sample counts do not support population tail-latency claims.

| Operation | Baseline median ms | Candidate median ms | Candidate p95 ms | Budget ms |
| --- | ---: | ---: | ---: | ---: |
| `deterministic_run_startup` | 0.671 | 0.301 | 1.021 | 5000.0 |
| `initial_index` | 4278.519 | 4281.155 | 4632.57 | 30000.0 |
| `incremental_index` | 3871.783 | 3501.322 | 5802.26 | 15000.0 |
| `lexical_search` | 872.604 | 669.319 | 846.736 | 5000.0 |
| `graph_traversal` | 708.967 | 599.068 | 741.23 | 5000.0 |
| `context_planning` | 1266.954 | 1037.479 | 1114.537 | 10000.0 |
| `daemon_app_startup` | 778.405 | 863.336 | 922.354 | 20000.0 |
| `protocol_readiness` | 79.511 | 100.555 | 229.405 | 10000.0 |
| `offline_replay` | 0.071 | 0.081 | 0.38 | 5000.0 |
| `filesystem_tool_read` | 2.357 | 3.25 | 3.752 | 2000.0 |
| `trace_recording` | 8.678 | 11.138 | 13.267 | 2000.0 |

Benchmark Python allocation peak: 25,614,381B;
isolated daemon Python allocation peak: 15,051,798B,
not OS RSS. Owned persistence 6,007,121B excludes
generated repository bytes. Provider calls 0 and network_used=false.
`deterministic_run_startup` measures its narrow fixture operation, not an entire
LLM task. Broad-regression-v1 has intentionally broad environment-specific
budgets; passing them is not proof of interactive10k indexing.

## Index scale, exact generated workloads

| Operation | 1,000 files ms | 10,000 files ms |
| --- | ---: | ---: |
| full_index | 23354.398 | 243618.17 |
| one_file_incremental | 8994.614 | 107706.486 |
| hundred_file_update | 10567.861 | 120602.545 |
| rename_storm | 10315.035 | 100195.585 |
| delete_storm | 8558.839 | 114468.046 |
| parser_version_invalidation | 11983.2 | 176705.239 |
| configuration_invalidation | 11006.727 | 237038.759 |
| watcher_overflow_recovery | 8974.489 | 104291.681 |
| cancellation | 8296.278 | 105832.465 |
| restart_recovery | 8460.268 | 106069.826 |

All10 scenarios passed at each size, including rename/delete storms, parser and
configuration invalidation, watcher overflow, cancellation and restart. Every
reported unnecessary reindex count was 0. Initial peak allocations were
101,945,807B and 1,031,901,477B respectively.
The 10k corpus is only 2,097,718B: file count and
index metadata, not large source blobs, dominate this synthetic workload.
One-file update still traverses repository/index state and takes107.7s under
instrumentation. That is an observed performance limitation, not a hidden pass
for a stricter interactive target. No explicit10k interactive latency SLO was
invented after measurement. 50k was NOT_RUN: the machine had about3.3GiB free
RAM after concurrent checks, while10k alone approached1.03GB of traced allocations.
Linear extrapolation is only a planning estimate, not measured50k behavior.

## Actual daemon read/auth/SSE load

The bounded load used100 seeded **pending history rows**, not100 completed agent
executions. Request mixes include valid reads, denied authentication and missing
runs. Status429/503 is expected admission evidence; throughput includes rejected
requests and is not successful work capacity.

| Clients | Requests | p50 ms | p95 ms | p99 ms | REST status counts | SSE status counts |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 10 | 50 | 24.645 | 161.611 | 187.411 | `{'403': 5, '200': 35, '404': 10}` | `{'200': 10}` |
| 50 | 250 | 141.668 | 585.22 | 605.59 | `{'403': 25, '200': 175, '404': 50}` | `{'200': 32, '503': 18}` |
| 100 | 500 | 127.448 | 249.101 | 350.265 | `{'403': 14, '200': 97, '404': 29, '429': 360}` | `{'200': 29, '429': 71}` |

SQLite integrity was `ok` and all 100 fixtures remained present. After five
quiescent samples, owned daemon RSS177,303,552B,57 threads and 532 handles remained;
peak observed handles1033. This includes warmed threadpool/HTTP state. A short run
cannot prove absence of a sustained daemon leak. The probe forcibly terminated
its own daemon (exit1), so it is not graceful-shutdown evidence. Approval-write,
artifact-download, saturated parallel execution and model-inference throughput
were not measured by this probe. Raw CPU/RSS/thread/handle time series are in
`http-load-results-final.json`; no process-count capacity was inferred from a fixture.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `benchmark-baseline` | 2026-11-17T20:26:00 | 2026-11-17T20:27:07 | 0 | 66.985 |
| `benchmark-compare` | 2026-11-17T20:27:07 | 2026-11-17T20:28:11 | 0 | 64.218 |
| `index-scale-medium` | 2026-11-17T20:28:11 | 2026-11-17T20:30:07 | 0 | 115.25 |
| `index-scale-large` | 2026-11-17T20:30:07 | 2026-11-17T20:54:41 | 0 | 1473.89 |
| `http-load` | 2026-11-17T19:55:07 | 2026-11-17T19:55:15 | 0 | 8.188 |
| `http-load-settled` | 2026-11-17T19:56:04 | 2026-11-17T19:56:18 | 0 | 14.266 |
