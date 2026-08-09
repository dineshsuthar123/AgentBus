# Performance measurement

AgentBus benchmarks are local, generated, and providerless. They measure
product overhead rather than model quality or remote latency.

```console
agentbus benchmark --group smoke --json
agentbus benchmark --group large --json
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
