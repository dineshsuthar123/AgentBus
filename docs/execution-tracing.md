# Execution Tracing

AgentBus v0.4 records a bounded, hierarchical execution trace for durable
runs. A trace is diagnostic evidence for inspection, replay, comparison, and
provenance verification. It is not execution truth: the durable SQLite run,
task, attempt, approval, tool, and event records remain authoritative.

Trace recording is designed to fail independently where possible. An optional
capture failure is reported without changing a successful task into a failed
task. A malformed or corrupted trace is never treated as valid replay input.

## Trace model

The `agentbus.trace` schema is versioned. A `Trace` owns:

- one root run span;
- ordered child spans;
- lifecycle events;
- replay checkpoints;
- causal links to source, replay, or fork traces;
- optional replay metadata.

Every `TraceSpan` has a trace and span ID, parent span ID, run ID, optional
task, worker, and invocation IDs, a type, status, timestamps, and a
deterministic sequence number. It can reference bounded inputs, outputs,
policy decisions, approvals, artifacts, cancellation state, resource usage,
and safe failure information.

Recorded span types include:

- run, planning, and task orchestration;
- provider request and response boundaries;
- structured model parsing;
- tool policy and managed tool invocation;
- approval waits;
- verifier and reviewer execution;
- Git mutation and integration;
- cancellation and cleanup;
- bounded custom lifecycle spans.

Parent relationships represent hierarchy. Sequence numbers establish a stable
total order within a trace. Links represent relationships such as replay-of or
forked-from without rewriting the source trace.

## What is captured

AgentBus captures only material needed for bounded diagnostics or replay:

- sanitized structured model envelopes and parsed values;
- policy decisions, capability facts, and approval references;
- bounded managed-tool request and result envelopes;
- safe repository patches and generated artifact hashes;
- verifier and reviewer structured results;
- task-graph and checkpoint state;
- safe cancellation and cleanup state;
- bounded resource measurements;
- protocol and configuration fingerprints;
- content hashes and redaction metadata.

Payloads are stored in the content-addressed trace object store next to the
configured state database, under `trace-objects`. Trace records refer to
objects by SHA-256 instead of embedding unrestricted values in SQLite, control
responses, logs, or VS Code documents.

The default object limit is 8 MiB, with a hard implementation limit of
64 MiB. Text, collections, nesting, references, and total trace items have
independent bounds. Identical sanitized objects are deduplicated.

## What is never captured

The trace boundary does not intentionally retain:

- bearer tokens, API keys, passwords, or provider credentials;
- hidden chain-of-thought;
- raw inherited environment variables;
- unrestricted prompts or provider SDK objects;
- unrestricted source trees;
- unbounded stdout or stderr;
- arbitrary external files;
- executable archive entries;
- remote telemetry.

Secret-shaped JSON fields and text are redacted before hashing and storage.
Private roots are replaced with safe labels. Binary or executable-like data
and values that still look secret after sanitization are rejected.

Source-like objects may be required to reproduce a patch or tool result. They
remain bounded and sanitized. Portable archives omit them by default and
require explicit source-content consent to include or import them.

## Content-addressed objects

Each object records:

- SHA-256 of its sanitized bytes;
- media type and byte count;
- redaction metadata;
- creation time;
- producing span IDs;
- retention classes;
- object schema version.

Writes use a temporary file and atomic replacement. Reads verify the file
length, content hash, metadata identity, and configured size bound. Object
paths are derived from validated lowercase hashes; traversal and
symlink/junction escape are rejected.

The available retention classes are `transient`, `run`, `failure`, `fixture`,
and `pinned`.

## Checkpoints

Runtime checkpoints are recorded after important durable transitions,
including plan creation, task-graph persistence, task or tool completion,
approval, verification, and integration where applicable. A checkpoint
contains versioned state references and ancestry metadata.

Checkpoint presence does not guarantee replayability. Replay validates:

- trace and checkpoint schema versions;
- checkpoint ancestry;
- required dependency completion;
- referenced object availability;
- repository base commit where one is required.

Partial replay reconstructs state outside the source repository. See
[Deterministic Replay](deterministic-replay.md).

## Replayability

Each span is classified as:

- `exactly_replayable`;
- `deterministically_substitutable`;
- `partially_replayable`;
- `observational_only`;
- `non_replayable`.

The classifier returns safe reasons, required and missing object hashes,
available substitutions, isolation requirements, and whether live-provider
consent would be required. A run is not advertised as offline replayable when
a required captured value is missing.

Examples:

- parsing and policy evaluation are exact when their required captured values
  and current descriptors exist;
- provider and MCP envelopes can be substituted without a live call;
- a mutating tool is simulated offline or explicitly rerun only in isolation;
- host timing and process scheduling are observational;
- an uncaptured external mutation is non-replayable.

## Inspection

Use the CLI without executing a replay:

```powershell
agentbus trace list --json
agentbus trace inspect <run-or-trace-id> --json
agentbus trace verify <run-or-trace-id> --json
```

The authenticated loopback control plane exposes bounded trace summaries,
span pages, individual span details, provenance, and replayability. It never
provides unrestricted object-store or SQLite access.

VS Code provides native Timeline, Replay Sessions, and Comparisons views.
Virtual documents show safe metadata and hashes, not captured payload values.

## Failure and cleanup

A failed, rejected, or cancelled run can still have useful trace and artifact
records. AgentBus does not reset, clean, delete, or roll back source workspace
files after failure. Trace replay also does not roll back prior external side
effects.

Trace garbage collection is plan-first:

```powershell
agentbus trace gc --json
agentbus trace gc --execute --json
agentbus trace gc --resume --json
```

The default policy keeps failures, the 100 most recent traces, and referenced
objects. Age and total-byte limits are optional. GC journals deletions,
protects referenced and active replay material, can resume after interruption,
and never deletes a source repository.

## Related documents

- [Deterministic Replay](deterministic-replay.md)
- [Run Provenance](run-provenance.md)
- [Trace Archives](trace-archives.md)
- [Regression Fixtures](regression-fixtures.md)
- [ADR 0010](adr/0010-deterministic-replay-and-provenance.md)
