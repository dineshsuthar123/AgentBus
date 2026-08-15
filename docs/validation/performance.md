# Performance validation

AgentBus performance validation is generated, local, providerless regression
evidence. It measures product overhead and does not claim model quality, remote
latency, or universal capacity.

```console
agentbus benchmark all --files 250 --iterations 5 --output .tmp/perf-baseline.json --json
agentbus benchmark all --files 250 --iterations 5 --baseline .tmp/perf-baseline.json --comparison-output .tmp/perf-scorecard.json --json
```

The full scorecard compares 11 metrics:

1. Daemon startup.
2. Initial indexing.
3. Incremental indexing.
4. Lexical search.
5. Graph traversal.
6. Context planning.
7. Deterministic-run startup.
8. Managed filesystem read invocation.
9. Offline replay.
10. Isolated daemon Python-allocation peak.
11. AgentBus-owned persistent storage size.

Generated repository bytes are excluded from persistent-storage measurements.
Reports include the environment, fixture dimensions and fingerprint, operation
samples, broad operation budgets, provider/network counts, and unavailable
measurements.

## Comparison policy

A regression must exceed both 1.75 times its baseline and the metric-specific
absolute noise tolerance. An improvement must fall below 0.60 times its
baseline and clear the same tolerance. Everything else is neutral. This broad
policy is designed to catch major release regressions without turning ordinary
CI noise into a product claim.

There is no opaque aggregate performance score. A release scorecard fails when
any available metric is classified as a regression. Unavailable metrics remain
visible warnings.

Baseline JSON is limited to a 2 MiB regular UTF-8 file and at most 100 operation
records. Both reports must use `benchmark all`, the same generated fixture,
operating system, architecture, Python implementation, and Python major/minor
series. Patch-version, dependency, processor-count, and iteration differences
are warnings. Incompatible evidence is rejected rather than normalized.

## Repository scale

Synthetic profiles contain deterministic Python source files:

| Profile | Files | Required use |
| --- | ---: | --- |
| `small` | 100 | Fast local diagnosis |
| `medium` | 1,000 | Required CI scale smoke |
| `large` | 10,000 | Explicit manual validation |
| `very-large` | 50,000 | Explicit manual validation |

```console
agentbus benchmark index-scale --size medium --iterations 1 --json
agentbus benchmark index-scale --size large --iterations 1 --json
```

The scale runner measures full index, one-file and 100-file updates, rename and
delete storms, parser-version and configuration invalidation, watcher overflow,
cancellation, and restart recovery. It verifies snapshot state and unnecessary
reindex work in addition to recording time, database size, and peak Python
allocations.

The RC acceptance performance smoke runs two 20-file, one-iteration full
benchmarks and requires all 11 metrics to compare with no broad regression.
That smoke verifies wiring and policy; it is not a stable cross-machine
benchmark.

## Interpretation and gaps

Run comparisons on an otherwise quiet machine and use multiple iterations for
manual evidence. Keep JSON outputs under an ignored temporary or release-
artifact directory, not in source control.

- Wall-clock timings vary with CPU scheduling, power policy, storage cache,
  antivirus, filesystem, and concurrent workload.
- Python allocation peaks do not equal total process RSS.
- Generated files do not model every parser mix, dependency graph, binary
  asset, generated tree, network filesystem, or repository history.
- Passing budgets does not prove acceptable end-user latency or throughput.
- Performance validation makes no live provider, network, or external-service
  measurement.

For expanded command semantics, see the [performance guide](../guides/performance.md).
