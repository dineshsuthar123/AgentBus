# ADR 0010: Deterministic Replay and Run Provenance

- Status: Accepted
- Date: 2026-07-28

## Context

AgentBus already persisted durable runs, task graphs, attempts, approvals, tool
audit records, cancellation state, events, worktrees, and final reports.
Debugging still required correlating those records manually with logs,
provider responses, repository patches, and host state. Existing evaluation
fixtures tested expected outcomes but could not reconstruct one historical
execution through the normal parsing and policy paths.

Several constraints shape the design:

- SQLite durable state must remain execution truth.
- Replay must not require Azure, Ollama, MCP, credentials, or network access.
- Trace capture must not retain credentials, hidden chain-of-thought,
  unrestricted prompts, whole repositories, raw environments, or unbounded
  output.
- Historical evidence must be immutable and tamper detectable.
- Partial replay must not mutate the source repository.
- Approval and policy safety cannot be bypassed by historical success.
- External systems, process scheduling, and host timing cannot be represented
  honestly as perfectly deterministic.
- Importing evidence must not execute it.
- Retention and cleanup must never delete user source or perform automatic
  destructive rollback.

## Decision

### Unified trace evidence

Add the versioned `agentbus.trace` model with hierarchical spans, deterministic
sequence numbers, events, checkpoints, links, typed references, artifacts,
resource usage, cancellation state, and safe failures.

Trace recording is diagnostic evidence, not execution truth. The existing
durable run, task, attempt, approval, tool, and event records remain
authoritative. Optional recording failures are reported independently.
Malformed or corrupted evidence is rejected for verification and replay.

### Sanitized content-addressed storage

Store replay material as bounded sanitized objects addressed by SHA-256.
SQLite records references and metadata rather than unrestricted payloads.

The object store:

- hashes post-sanitization canonical bytes;
- deduplicates identical objects;
- validates content and metadata on every read;
- writes atomically;
- rejects traversal, links, binary/executable content, and secret material;
- uses explicit retention classes;
- lives beside the configured state database, not inside the source
  repository by default.

AgentBus does not capture whole repository snapshots. It records selected
structured envelopes, patches, artifacts, and checkpoint state required for
diagnostics or replay.

### Tamper-evident provenance

Seal terminal traces with a versioned provenance manifest. Fingerprint runtime,
configuration, provider routes, tool descriptors, policy, protocols, task
graph, event range, final Git tree, artifacts, and replayability.

Use a domain-separated ordered SHA-256 chain over canonical span, blob,
approval, tool audit, and artifact integrity entries.

This detects modification of covered evidence. It does not provide a digital
signature, trusted timestamp, external identity, authorship proof, or
attestation of a provider's truthfulness.

### Conservative replayability

Classify each span and run as exactly replayable, deterministically
substitutable, partially replayable, observational only, or non-replayable.
Return bounded explanations, missing hashes, substitutions, isolation needs,
and live-consent requirements.

Missing required data is non-replayable. Classification never treats a live
provider call as an implicit fallback.

### Providerless replay engine

Add `agentbus.replay` with strict, offline, verify, and simulate modes.

Replay uses captured structured provider and MCP envelopes, runs normal
parsing and current-policy logic, and validates structured verifier and
reviewer results. Configured low-level engine callbacks can rerun deterministic
verifier or reviewer logic. Session records include per-span actions,
substitutions, drift, failures, and provider/network call counters.

Offline mode simulates recorded filesystem and Git mutations and reuses
bounded captured test/process results by default. A sandbox rerun must be
explicit and requires a configured replay executor and isolated workspace.
Uncaptured external mutations are rejected.

Replay cancellation is cooperative at span boundaries. Terminal sessions are
durable and are not rerun after restart.

### Checkpoint isolation

Record versioned checkpoints after important durable transitions. Partial
replay validates ancestry and dependencies, copies SQLite state, and
reconstructs an AgentBus-owned Git worktree outside the source repository when
a base commit is needed.

Public APIs expose a stable isolation label, not a private absolute path.
Replay state is not automatically destructively deleted; cleanup remains an
explicit ownership-validated operation.

### Immutable forks and structured comparison

A fork retains source provenance links, sanitizes and hashes an allowlisted set
of changed inputs, creates a new run and trace, seals new provenance, and
automatically compares source and fork. It never mutates the source trace.

Changes that can invalidate authorization do not reuse historical approval as
authority. A live provider route requires explicit consent and a configured
live replay executor. Control-plane and VS Code replay remain offline.

Comparison aligns semantic spans and classifies structured field differences.
It reports hashes and safe summaries instead of compared payload values.

### Portable archives and fixtures

Use a deterministic constrained ZIP format with a manifest, trace,
provenance, assertions, protocols, and selected content-addressed objects.

Import validates entry count, decompressed size, compression ratio, paths,
duplicates, file type, schema, hashes, object inventory, source classification,
protocols, and provenance before persistence. It never extracts executable
files or starts replay.

Source-like objects are omitted by default. Including or importing them
requires explicit consent and carries source and license warnings.

A successful run can become a regression fixture with derived assertions for
status, score, verifier, reviewer, file scope, policy, tools, patch hashes, and
safety outcomes.

### Interfaces

Expose trace, replay, comparison, provenance, archive, fixture, retention, and
verification through:

- CLI commands;
- authenticated loopback control-plane endpoints;
- generated OpenAPI, JSON Schema, and TypeScript bindings;
- native VS Code Timeline, Replay Sessions, and Comparisons views;
- read-only bounded virtual documents.

No interface provides unrestricted object-store, source-tree, or SQLite
access.

## Consequences

Benefits:

- one causal model for run, provider, tool, policy, approval, verification,
  review, integration, cancellation, and cleanup evidence;
- providerless reproduction of deterministic paths;
- explainable missing inputs and drift;
- tamper detection for trace objects and durable evidence;
- safe checkpoint and fork experiments without rewriting history;
- portable regression evidence;
- native time-travel debugging without a large webview;
- cross-platform deterministic acceptance and CI.

Costs:

- additional SQLite and content-addressed storage;
- protocol and schema maintenance;
- explicit retention and source-consent workflows;
- conservative replay rejection when evidence is incomplete;
- isolated replay state can require explicit cleanup;
- comparison and replay semantics must evolve compatibly with tool and policy
  protocols.

Limitations:

- host scheduling, latency, and resource measurements are not exact;
- external services can only be substituted when bounded envelopes exist;
- local process behavior may differ across operating systems;
- a valid provenance root is not proof of author or trusted origin;
- a compromised capture process can produce internally consistent false
  evidence;
- replay does not undo original filesystem or external side effects;
- this remains defense in depth, not virtual-machine or kernel isolation.

## Rejected alternatives

- **Use logs as the replay format.** Rejected because logs are presentation
  oriented, incomplete, weakly typed, and may expose sensitive strings.
- **Store all payloads directly in SQLite.** Rejected because it increases
  secret, size, duplication, and corruption risk and prevents independent
  content verification.
- **Capture complete repository or VM snapshots.** Rejected as excessive,
  invasive, expensive, and outside the local developer-tool threat model.
- **Call the original provider when captured input is missing.** Rejected
  because it breaks offline guarantees, requires credentials, introduces new
  side effects, and disguises non-replayability.
- **Treat a previous approval as permanent replay authority.** Rejected because
  capability, policy, scope, budget, and artifact facts may have changed.
- **Rerun mutations in the source repository.** Rejected because replay must
  not damage user state or rewrite historical evidence.
- **Automatically reset or delete state after replay.** Rejected because
  ownership and user intent cannot be inferred safely and destructive cleanup
  can erase evidence or user changes.
- **Use blockchain or mandatory remote attestation.** Rejected because local
  integrity detection does not require distributed consensus, cloud storage,
  telemetry, or a false authorship claim.
- **Use a large VS Code webview.** Rejected in favor of native TreeViews,
  virtual documents, and constrained diff editors with smaller attack and
  maintenance surfaces.

## Related documents

- [Execution Tracing](../execution-tracing.md)
- [Deterministic Replay](../deterministic-replay.md)
- [Run Provenance](../run-provenance.md)
- [Trace Archives](../trace-archives.md)
- [Regression Fixtures](../regression-fixtures.md)
