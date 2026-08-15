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

## Release baseline scorecard

Create a reusable baseline with the full generated benchmark, then compare a
later run using the same fixture, operating system, architecture, and Python
major/minor series:

```console
agentbus benchmark all --files 250 --iterations 5 --output .tmp/perf-baseline.json --json
agentbus benchmark all --files 250 --iterations 5 --baseline .tmp/perf-baseline.json --output .tmp/perf-current.json --comparison-output .tmp/perf-scorecard.json --json
```

The scorecard compares daemon startup, initial and incremental indexing,
lexical search, graph traversal, context planning, deterministic-run startup,
filesystem tool invocation, offline replay, isolated daemon Python-allocation
peak, and AgentBus-owned persistent storage. Generated repository bytes are not
counted as AgentBus persistent storage.

Each available metric is classified as `regression`, `improvement`, or
`neutral`. A regression must exceed both `1.75x` the baseline and a
metric-specific absolute noise floor; an improvement must fall below `0.60x`
and clear the same noise floor. These deliberately broad boundaries tolerate
ordinary CI variance and detect major changes, not small optimization claims.
Skipped or unmeasurable evidence is reported as a warning rather than estimated.
The scorecard contains no opaque aggregate performance score.

Baseline loading is bounded to a 2 MiB regular UTF-8 JSON file. Reports from a
different fixture, operating system, machine architecture, Python
implementation, or Python major/minor series are rejected rather than compared.
Patch-version, dependency, processor-count, and iteration differences remain
visible warnings. Keep baseline and scorecard artifacts outside commits unless
they are intentionally reviewed release evidence.

## Reliability soak profiles

The quick soak is suitable for local development and remains the default:

```console
agentbus soak --profile quick --json
```

The release-candidate profile has a ten-minute scheduling limit and a bounded
run cap. It repeatedly exercises deterministic tasks, managed filesystem tools,
exact approvals, a synthetic local stdio MCP peer, indexing, offline replay,
cancellation, owned worktree cleanup, and durable daemon restart recovery:

```console
agentbus soak --profile release-candidate --json
```

Longer manual runs are explicit. For example, this sets a two-hour limit while
retaining all local, providerless, and bounded behavior:

```console
agentbus soak --profile release-candidate --duration 7200 --runs 10000 --json
```

The report records before, peak, and after values for AgentBus-owned child
processes and worktrees, state and index databases, trace storage, Python
allocation memory, threads, and process handles where the platform exposes a
safe measurement. It also reports stale leases, event gaps, and cleanup
failures. An unavailable handle count is reported as unmeasurable rather than
estimated. Long soaks are manual release evidence and are not part of ordinary
pytest invocation.

## Reliability scorecard

The release scorecard runs the generated validation corpus and the bounded
quick lifecycle profile together:

```console
agentbus validate reliability --json
agentbus validate reliability --repository C:\path\to\local-repository --json
```

Explicit local repositories are read and indexed into disposable storage; the
scorecard does not edit their source trees. Structured output records scenarios,
generated fixtures, supplied local repositories, failures, owned process and
worktree leaks, durable-database and repository-index integrity, replay,
cancellation, restart recovery, latency, and memory where measurable. Overall
classification is `PASS`, `PASS_WITH_WARNINGS`, or `FAIL`. AgentBus does not
collapse this evidence into an opaque numerical reliability score.

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
