# ADR 0011: Local Repository Intelligence Engine

- Status: Accepted
- Date: 2026-08-08

## Context

AgentBus already had a safe repository scanner and bounded context-pack builder,
but repeated runs rediscovered the same files and lacked durable symbol,
dependency, ownership, architecture, impact, and test-selection evidence. Large
or mixed-language repositories need more selective context without uploading
source or requiring a hosted indexing service.

The runtime must remain safe when indexing is absent or partial. Repository
intelligence cannot become a reason to execute build metadata, cross workspace
boundaries, leak protected files, download models, or weaken final verification
and review.

## Decision

Implement an optional local repository-intelligence subsystem with:

1. portable repository/workspace identities and immutable snapshot identities;
2. a versioned SQLite metadata and graph store beside AgentBus state;
3. contained static discovery for Python, Node, Java, Go, and monorepo metadata;
4. bounded, non-executing parsers for Python, TypeScript/JavaScript, Java, and Go;
5. typed imports, exports, calls, references, inheritance, implementation, test,
   ownership, configuration, and generated-from edges;
6. incremental content-hash invalidation, leases, cancellation, repair, and
   explicit freshness states;
7. deterministic lexical and structural retrieval with optional local semantic
   evidence;
8. bounded context, impact, and test-selection plans with confidence,
   uncertainty, evidence, and truncation;
9. additive CLI, authenticated control-plane, VS Code, trace, provenance,
   replay, evaluation, and CI integration;
10. compatibility fallback to the existing scanner and context-pack builder.

The index stores source-derived metadata and hashes, not source snapshots.
Control-plane responses and portable evidence remain source-free. Semantic
retrieval is disabled by default and accepts only an explicit local provider
that declares no off-device source transfer; vectors are not persisted.

## Safety boundaries

Repository discovery reuses the managed-tool contained-path resolver and rejects
protected paths, links/junctions, traversal, device/UNC/alternate-stream syntax,
and out-of-root resolution. Metadata and source parsers never execute repository
code or package managers. Work, diagnostics, progress, API pages, graph
traversal, context, and retained snapshots are bounded.

Workspace identity is part of every snapshot. Runtime and replay explicitly load
the index beside the selected run state and validate scope before using claims.
Stale, partial, corrupted, and incompatible states remain visible. No index
operation resets or rolls back repository changes.

## Alternatives considered

### Replace scanning with an index

Rejected. Indexes can be absent, stale, partial, or incompatible. Keeping the
scanner compatibility path preserves availability and a simpler safety floor.

### Hosted indexing or remote embeddings

Rejected. Uploading source conflicts with the local-first privacy model and
would make normal execution depend on network credentials and service state.

### Invoke language servers and build tools

Rejected for this milestone. They can execute repository-controlled code,
download dependencies, mutate caches, and produce environment-dependent output.
The static parser interface can be extended later without changing persisted
domain contracts.

### Persist full source or embedding vectors

Rejected. Metadata, hashes, ranges, and evidence are sufficient for retrieval
planning while reducing secret, privacy, portability, and retention risk.

## Consequences

Benefits:

- repeated runs reuse unchanged parsing work;
- context and review are dependency-aware and explainable;
- impact and test selection can fail safely toward broader verification;
- trace and replay can identify repository-understanding drift;
- normal indexing remains providerless, network-free, and cross-platform.

Costs and limitations:

- SQLite schema and parser versions require migration and compatibility policy;
- static analysis remains incomplete for dynamic and generated behavior;
- large repositories can produce partial or truncated evidence;
- parser maintenance grows with supported language syntax;
- index freshness must be checked at every consumer boundary;
- benchmark fixtures do not establish production accuracy guarantees.

## Validation

The decision is covered by multilingual parser and fixture tests, migration and
incremental invalidation tests, graph/retrieval/impact/context tests, Windows and
Ubuntu path-security tests, offline control acceptance, deterministic evaluation,
real-daemon VS Code Electron E2E, protocol freshness, and VSIX audit.
