# ADR 0005: Evaluation And Regression Harness

## Status

Accepted

## Context

AgentBus has unit-tested providers, agents, durable state, parallel scheduling, worktrees, integration, verification, review, artifact filtering, and Git finalization. Component tests alone cannot answer whether a complete runtime variant performs a representative software-engineering task correctly, safely, efficiently, and reproducibly. Model-only judging is also insufficient: a reviewer can overlook functional defects, accept unrelated changes, or reject valid work for reasons outside the current task.

The product needs a repeatable way to compare single-agent, multi-agent, durable, parallel, provider, retry, fallback, and prompt/configuration variants without creating another orchestration engine.

## Decision

Add `agentbus.evaluation` as a measurement layer over the existing runtime. An `EvaluationRunner` creates an isolated repository, invokes the selected existing runtime path, collects structured reports and usage, evaluates deterministic assertions, calculates a transparent score, and stores a sanitized versioned result. Evaluation case execution is sequential; AgentBus task parallelism remains the scheduler's responsibility.

### Deterministic Assertions

Deterministic assertions are the primary correctness and safety oracle. They cover terminal state, verifier and reviewer outcomes, filesystem contents, changed-file scope, generated artifacts, repository boundaries, Git/PR side effects, approvals, attempt behavior, recovery, conflicts, source immutability, secret patterns, and resource limits. The model reviewer remains mandatory where the runtime requires it and contributes to scoring, but reviewer approval cannot override a failed deterministic safety assertion.

Expected failure cases, such as traversal rejection, integration conflict, and approval waiting, pass only when the exact safe state and side effects are observed. This distinguishes successful safety behavior from successful implementation behavior.

### Offline By Default

`core-offline` uses deterministic fake providers keyed by case, task, role, and attempt. Exact routing prevents parallel workers from consuming another task's response. Fake results include normalized usage and can inject malformed output, transport failure, verifier failure, reviewer rejection, worker crashes, lease expiry, post-commit interruption, integration interruption, merge conflict, approval rejection, and fallback attribution.

Each case receives a fresh copy of a compact local fixture. The copy is initialized as its own Git repository, so parent repository state cannot enter changed-file or reviewer context. Temporary deletion requires a matching ownership marker and repository path. Offline evaluation does not construct or invoke a network provider.

Offline is the CI default because it is bounded, attributable, reproducible, credential-free, and suitable for testing state transitions that would be expensive or unreliable to induce remotely.

### Live Opt-In And Budgets

Live evaluation requires all of the following:

- an explicitly selected live suite;
- an explicitly selected live variant;
- the `--live` consent flag;
- positive request, token, and wall-clock budgets.

Provider wrappers reserve a request and a conservative prompt/output token estimate before every call. Wrapping survives provider-factory cloning in task worktrees and final integrated review. Actual usage is recorded afterward and can also exhaust the budget. Partial evaluation results are saved incrementally. Live evaluation never enables PR creation, pushing, deployment, or fallback unless the variant explicitly requests fallback.

### Score

The default weighted score is:

- functional correctness: 30;
- tests: 20;
- scope discipline: 15;
- safety: 15;
- recovery/integration: 10;
- final review: 5;
- efficiency: 5.

For each applicable dimension, points equal `weight * passing assertions / applicable assertions`. A dimension with no applicable assertion is neutral rather than penalized. Weights are typed and must sum to 100. Any hard safety failure sets the total score to zero and fails the case. Raw assertions and metrics remain authoritative; the aggregate score is a summary, not a replacement for diagnostics.

### Storage And Privacy

Evaluation results use schema-versioned JSON under `.agentbus/evaluations`, separate from normal durable SQLite state. Writes are atomic. Stored data includes suite/run IDs, timestamps, AgentBus commit, configuration fingerprint, variant, case outcomes, assertions, metrics, scores, and safe artifact references.

Results exclude task prompts, unrestricted source snapshots, environment dumps, API keys, SDK objects, and raw provider responses. All free-form metadata passes through the existing redaction layer. Export reads the sanitized typed record rather than copying runtime logs.

The configuration fingerprint hashes planner, coder, reviewer, and task-review templates; the action schema; and secret-free workflow, route, retry, fallback, and prompt version settings. Timestamps and run IDs are not part of the fingerprint.

### Baselines

A named baseline is a complete sanitized evaluation run. Creating a baseline does not mutate a run, and replacing one requires an explicit `--replace`. Comparison reports critical quality/safety transitions separately from threshold-based score, token, latency, unrelated-file, and retry changes. A missing previously evaluated case is a critical regression.

Latency and usage comparisons are thresholded because they can vary across hosts and live providers. Deterministic offline reproducibility checks normalize expected timestamps, run IDs, elapsed durations, and temporary paths before equality comparison.

## Consequences

Positive consequences:

- Runtime milestones can be gated on end-to-end behavior rather than component coverage alone.
- Workflow and provider variants share one case and metric format.
- Safety failures cannot be hidden by a high average score or reviewer approval.
- Crash, lease, integration, approval, and fallback behavior can be tested without paid calls.
- Prompt and configuration changes can be tied to objective baselines.

Tradeoffs:

- Compact fixtures do not represent the scale, dependency complexity, or ambiguity of arbitrary repositories.
- Fake provider success measures orchestration and policy behavior, not model intelligence.
- Live latency and token usage are not perfectly reproducible.
- Case execution is sequential, so large suites will take longer.
- The core score omits monetary cost because no stable deployment-independent price source exists.

Passing the offline or live fixtures is evidence of behavior under those cases. It does not guarantee production correctness, security, model quality, or performance on a different repository.

## Alternatives Considered

### Model Reviewer As The Only Judge

Rejected because it is nondeterministic and cannot independently prove file contents, test results, repository scope, resource limits, or absence of forbidden side effects.

### A Separate Benchmark Orchestrator

Rejected because it would measure a new implementation instead of AgentBus. The harness must invoke the production agents, engine, scheduler, worktrees, verifier, reviewer, and reporting paths.

### Live Provider Evaluation By Default

Rejected because it creates cost, credential, availability, rate-limit, and reproducibility risks. Live measurements remain valuable only as explicit bounded smoke tests.

### Automatic Fixture Rollback Or Repository Cleanup

Rejected because destructive cleanup is unsafe without exact ownership. The harness deletes only marker-owned temporary roots and preserves them when requested.

## Future Work

- Add opt-in, license-reviewed real-world repository benchmark datasets without arbitrary downloading.
- Add user-supplied pricing tables outside the core quality score.
- Add statistical aggregation over repeated live runs and confidence intervals.
- Add case sharding only after proving deterministic isolation.
- Add schema migration support if JSON result evolution exceeds simple versioned readers.
- Expand language, build-system, and repository-size coverage while retaining local ownership and budget controls.
