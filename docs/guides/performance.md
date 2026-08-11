# Performance measurement

AgentBus benchmarks are local, generated, and providerless. They measure
product overhead rather than model quality or remote latency.

```console
agentbus benchmark all --json
agentbus benchmark search --size small --json
```

Reports include AgentBus and Python versions, platform, fixture dimensions,
startup, full and incremental indexing, search, context planning, run overhead,
offline replay, memory where available, and database sizes. Budgets are broad
regression tripwires, not universal performance promises.

For repeatability, close unrelated heavy processes, use the same Python and Git
versions, run on the same storage class, and compare medians from multiple
runs. Never commit transient benchmark output or benchmark clones.

The synthetic fixtures contain generated source only. Real-repository
benchmarking is opt-in and must follow the pinned-SHA and license review process
in [Benchmarking real repositories](../benchmarking-real-repositories.md).

## Repository index scale

The index-scale benchmark exercises full and incremental indexing against a
disposable generated repository. The default profile contains 1,000 files:

```console
agentbus benchmark index-scale --size medium --json
```

The larger profiles are intentionally manual. They are not included in
`agentbus benchmark all` or the normal test suite:

```console
agentbus benchmark index-scale --size large --output .tmp/index-10k.json --json
agentbus benchmark index-scale --size very-large --output .tmp/index-50k.json --json
```

`medium`, `large`, and `very-large` generate 1,000, 10,000, and 50,000 source
files respectively. `--files` may select a bounded custom size for local
diagnosis. Generation and indexing use a unique temporary directory, make no
provider or network calls, and remove the generated repository after the report
is assembled. The JSON report contains no temporary workspace path.

Each run measures full indexing, a one-file edit, a 100-file edit, rename and
delete storms, parser-version and project-configuration invalidation, watcher
overflow recovery, cooperative cancellation, and restart recovery. Per-scenario
records include elapsed time, indexed/reused/invalidated files, and the number
and ratio of files reindexed outside the expected direct or dependency-driven
invalidation set. The report also records final database size and peak Python
allocation memory when the runner can own tracing.

Timing and memory values are observations, not portable pass/fail thresholds.
Correctness fails the benchmark when recovery or snapshot-state checks fail or
when avoidable reindex work is observed. Keep generated JSON reports outside
commits unless they are deliberately reviewed documentation fixtures.
