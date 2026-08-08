# Deterministic Replay

AgentBus v0.4 can replay a recorded execution without calling Azure, Ollama,
remote MCP servers, or another network service. Replay uses captured,
sanitized objects to drive the normal structured parsing, policy, verifier,
reviewer, and state-transition code paths where those paths are deterministic.

Replay is diagnostic reconstruction, not a promise that every host or external
side effect can be reproduced exactly.

## Before replaying

Inspect replayability and verify provenance first:

```powershell
agentbus trace inspect <run-or-trace-id> --json
agentbus trace verify <run-or-trace-id> --json
```

The replayability result explains:

- the run and per-span classification;
- missing required object hashes;
- provider or tool substitutions;
- isolated-workspace requirements;
- unresolved nondeterminism;
- whether a requested live route would require consent.

A missing required input fails closed. Replay does not silently call a live
provider to fill the gap.

## Replay modes

### Strict

`strict` accepts only traces classified as exactly replayable or
deterministically substitutable. Missing objects, partially replayable host
behavior, incompatible schemas, and policy drift that violates strict
expectations stop the replay.

```powershell
agentbus replay <run-or-trace-id> --mode strict --json
```

### Offline

`offline` is the normal providerless mode:

- captured provider and MCP envelopes replace live calls;
- captured model values run through structured parsing;
- current deterministic policy is evaluated;
- pure reads can reuse bounded captured results;
- recorded filesystem and Git mutations are simulated by default;
- bounded captured test and process results are reused rather than re-executed;
- verifier and reviewer structured results are replayed;
- provider and network call counters remain zero.

An explicit per-tool sandbox-rerun strategy can be used by internal APIs where
a replay executor and isolated workspace are available. Offline mode never
falls back to mutating the source repository.

```powershell
agentbus replay <run-or-trace-id> --mode offline --json
```

### Verify

`verify` reconstructs captured evidence for verification-oriented replay. It
still remains providerless and validates required inputs and protocols.
Verification cannot prove that an uncontrolled external service would return
the same value today.

```powershell
agentbus replay <run-or-trace-id> --mode verify --json
```

### Simulate

`simulate` replays lifecycle transitions and simulates tool mutations. It is
the most conservative mode for traces containing behavior that cannot be
safely reproduced. Simulation is evidence about recorded control flow, not an
execution of the original external side effects.

```powershell
agentbus replay <run-or-trace-id> --mode simulate --json
```

## Provider and model substitution

Provider response spans reference bounded `CapturedModelEnvelope` objects.
Replay validates envelope schema and content hashes, substitutes the captured
value, and records the substitution. A model-parse span then processes its
captured raw value through normal structured validation.

Provider request latency, remote scheduling, and current deployment behavior
are not reproduced. Replay sessions expose `provider_calls` and
`network_calls`; providerless success requires both to be zero.

Credentials are not replay inputs and are never required for offline replay.

## Tool and policy replay

Every tool span has one explicit replay outcome:

- `reuse_captured`: use a bounded captured result;
- `rerun_sandbox`: invoke a configured replay executor in isolation;
- `simulate_mutation`: preserve state transitions without the side effect;
- `reject`: stop because no safe strategy exists.

Offline defaults are intentionally conservative:

- pure reads may be reused;
- filesystem and Git mutations are simulated;
- captured test and process results may be reused without launching a process;
- captured external MCP responses may be substituted;
- uncaptured network or external mutations are rejected.

Current policy is evaluated against captured invocation facts. Replay records
policy, capability, descriptor, and constraint drift. A current denial blocks
the historical action. Expanded capabilities require fresh authorization.

Historical approvals are evidence, not reusable blanket authority. Forks that
change policy, resource budgets, tool responses, or approval decisions
invalidate affected historical approval assumptions.

## Checkpoint replay

List checkpoints with trace inspection, then select one by ID:

```powershell
agentbus replay <run-or-trace-id> --mode offline --from <checkpoint-id> --json
```

The CLI also accepts a span or task selector and the aliases `beginning`,
`pre-verifier`, and `pre-integration` when the trace contains a compatible
selection.

Partial replay:

1. validates trace and checkpoint schema versions;
2. validates checkpoint ancestry and dependencies;
3. copies durable state to an isolated SQLite database;
4. creates an AgentBus-owned replay worktree when a base commit is required;
5. starts from the selected deterministic sequence;
6. keeps the source repository unchanged.

The replay root is outside the source repository. Control-plane responses
expose only `daemon_managed_temporary_workspace`, never its private absolute
path. AgentBus does not perform automatic destructive cleanup; removal of
AgentBus-owned replay state must be an explicit, separately reviewed action.

Partial replay of an imported archive is rejected unless a compatible
repository has been reconstructed separately.

## Forked replay

A fork derives a new run and trace while preserving a `forked_from` link to
the source. The source trace and terminal attempt history remain immutable.

```powershell
agentbus replay <run-or-trace-id> `
  --mode offline `
  --fork `
  --change 'resource_budgets={"invocations_per_run":64}' `
  --json
```

Supported changed-input names are:

- `task_text`;
- `model_route`;
- `deterministic_provider_profile`;
- `policy_configuration`;
- `resource_budgets`;
- `approval_decisions`;
- `tool_response`;
- `selected_source_patch`;
- `retry_limit`.

The changed values are sanitized and content hashed. Public reports list only
changed input names and hashes. A fork receives a new trace ID, sealed
provenance, and an automatic structured comparison with the source.

Azure or Ollama routes are never enabled implicitly. The CLI requires
`--live-provider-consent` for a live route, and replay still fails if no
explicit live replay executor is configured. The control plane and VS Code
offline commands send `live_provider_consent=false`.

## Comparing results

Compare any persisted run or trace identifiers:

```powershell
agentbus compare <left-run-or-trace> <right-run-or-trace> --json
```

Comparison uses semantic span identity and structured fields rather than a
text-only diff. Categories include expected, configuration, policy, model,
tool, environment, ordering, output, regression, improvement, and unknown
drift. Compared payload values are not rendered; reports expose hashes and
bounded summaries.

## Archive replay

An imported archive is validated and stored but never executed automatically:

```powershell
agentbus trace import run.agentbus-trace --json
agentbus replay run.agentbus-trace --mode offline --json
```

If the archive declares and actually contains source-like objects, both import
and archive replay require `--allow-source-content`.

## Cancellation and recovery

Replay cancellation is cooperative at deterministic span boundaries. A
cancelled session records completed span results, safe failure information,
and zero provider/network calls. Terminal replay sessions and fork
comparisons persist in SQLite and remain available after daemon restart.

Restart never turns a terminal replay back into pending and never reruns a
terminal successful source task.

## Nondeterminism limits

AgentBus records whether wall clock, UUIDs, randomness, mapping or filesystem
order, scheduling, environment, temporary paths, Git configuration, locale,
line endings, providers, MCP, and tool output order were controlled, captured,
substituted, observed, or unresolved.

Host CPU timing, process scheduling, current external services, and
uncaptured side effects are not exactly replayable. Reports retain this
limitation instead of treating a matching final status as proof of identical
execution.

## Repository-intelligence drift

When a run used repository intelligence, its trace captures source-free
snapshot, graph, retrieval, context-plan, and context hashes. Offline replay
reuses this captured evidence without provider or network calls. If the exact
local workspace index is available, replay may also compare current evidence and
report bounded drift categories:

- `index_snapshot_drift`;
- `graph_drift`;
- `retrieval_result_drift`;
- `context_plan_drift`.

Drift does not mutate the original trace, rerun a provider, or silently replace
captured evidence. An unavailable current index leaves captured evidence usable
and reports that comparison was unavailable. A different workspace index is
never substituted. See [Context Planning](context-planning.md).

## Related documents

- [Execution Tracing](execution-tracing.md)
- [Run Provenance](run-provenance.md)
- [Trace Archives](trace-archives.md)
- [Regression Fixtures](regression-fixtures.md)
