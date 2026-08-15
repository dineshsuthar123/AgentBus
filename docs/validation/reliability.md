# Reliability validation

Reliability validation combines the offline generated repository corpus with a
bounded deterministic lifecycle soak. It reports inspectable evidence instead
of collapsing results into an opaque numerical score.

```console
agentbus validate reliability --json
agentbus validate reliability --runs 10 --parallelism 2 --repository-files 20 --json
agentbus validate reliability --repository C:\work\authorized-repository --json
```

Each explicit local repository is canonically resolved and statically indexed
into disposable storage. At most 32 unique local repository paths may be added
to one scorecard. Source trees are not modified and repository setup commands
are not executed.

## Lifecycle evidence

The deterministic soak exercises durable task state, managed read/write/test
tools, exact approvals, a synthetic local MCP peer, index updates, trace
writing, offline replay, intentional cancellation, daemon restart and event
cursor recovery, lease release, and owned worktree cleanup.

The scorecard reports:

- completed repository and lifecycle scenarios with latency samples;
- AgentBus-owned process and Git worktree leaks;
- durable-state and index SQLite quick-check, schema, and foreign-key results;
- replay, cancellation, and restart attempts, passes, and failures;
- event gaps, stale leases, cleanup failures, and failed lifecycle cycles;
- before, peak, and after database, trace, process, worktree, thread, handle,
  and Python-allocation observations where measurable;
- generated fixture and explicit real-local repository evidence separately.

Unavailable measurements remain `PASS_WITH_WARNINGS`; failed integrity, leak,
replay, cancellation, restart, or lifecycle evidence produces `FAIL`.

## Bounded profiles

| Profile | Duration limit | Run cap | Parallelism | Generated files |
| --- | ---: | ---: | ---: | ---: |
| `quick` | 30 seconds | 10 | 2 | 20 |
| `release-candidate` | 600 seconds | 10,000 | 2 | 100 |

The public `validate reliability` command uses the quick profile and accepts
bounded overrides. The underlying hard ceilings are 10,000 runs, parallelism
32, 86,400 seconds, and 50,000 generated files. The Python-allocation growth
budget is 128 MiB plus 1 MiB for each completed cycle.

The RC acceptance gate intentionally uses a smaller smoke of two runs,
parallelism one, and 20 generated files. It proves the release workflow is
wired correctly; it does not replace the ten-minute manual RC soak:

```console
agentbus soak --profile release-candidate --json
```

Longer durations are explicit manual evidence. They remain providerless and
local, but they consume more CPU, memory, storage, and time.

## Reproduction guidance

Use the same operating system, architecture, Python major/minor, Git version,
storage class, seed, repository fixture, run count, and parallelism when
comparing runs. Close unrelated heavy workloads and retain the structured JSON
outside the repository. A timing change alone is diagnostic evidence, not a
portable reliability failure.

The required CI matrix runs core tests on CPython 3.11 through 3.14, dedicated
Windows and Linux security boundaries, sandbox stress, MCP adversarial tests,
SQLite contention, repository scale, replay integrity, and RC acceptance. The
local milestone verification ran on Windows AMD64 with CPython 3.14. CI jobs
must complete before claiming Ubuntu or another Python version passed.

## Known gaps

- The quick and RC profiles are bounded samples, not proofs of indefinite
  uptime, exactly-once execution, or distributed consensus.
- Python allocation tracking is not total process RSS and may exclude native
  library or child-process memory.
- Handle and process observations differ by platform and privilege level.
- Successful cancellation cannot undo a tool or external side effect that
  completed before cancellation was observed.
- Synthetic repositories do not reproduce every filesystem, network share,
  antivirus, container, enterprise Git, or very-large monorepo environment.
- Passing deterministic providers does not establish live-provider reliability
  or remote-service availability.

See [real repository validation](real-repositories.md) for source scope and
[performance validation](performance.md) for baseline comparison rules.
