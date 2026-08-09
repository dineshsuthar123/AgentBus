# Changelog

All notable AgentBus changes are documented here. The project follows semantic
versioning; Python prereleases use PEP 440 spelling (`0.6.0b1`) while user-facing
release names use `0.6.0-beta.1`.

AgentBus is pre-1.0. A minor version is a compatibility line, and a later minor
may include documented breaking changes. See the
[compatibility policy](docs/reference/compatibility.md).

## [0.6.0-beta.1] - Unreleased

### Added

- Offline-first `setup`, deterministic `quickstart`, demo repositories, layered
  configuration commands, stable product errors, and actionable `doctor` checks.
- Explicit package, Python, protocol, schema, migration, daemon, and VS Code
  compatibility diagnostics.
- Safe daemon lifecycle, bounded rotated logs, sanitized support bundles, and
  ownership-validated cleanup without automatic source rollback.
- Generated large-repository benchmarks, regression budgets, bounded soak
  checks, and machine-readable performance reports.
- Reproducible wheel, sdist, and VSIX audits plus a non-publishing
  `release-check` gate.
- Native VS Code walkthrough, safe settings, installation checks, and isolated
  fresh-profile product acceptance with restart recovery.
- User-oriented installation, quickstart, workflow, reference,
  troubleshooting, security, and contributor documentation.

### Changed

- Runtime dependency extras are separated by product capability.
- Deterministic mode is the default onboarding path; live providers remain
  explicit and are never required for CI.
- Public-beta configuration requires enforced tool policy and rejects secrets,
  unknown keys, unsafe workspace redirects, and configuration symlinks.

### Security

- Release audits inspect tracked files, distributions, VSIX contents, logs,
  support bundles, settings, migrations, registries, paths, and subprocess
  specifications for secrets and runtime artifacts.
- Cleanup remains non-destructive to repositories and unknown user data.

## [0.5.0] - Repository intelligence milestone

- Added a local, providerless index for Python, TypeScript/JavaScript, Java,
  and Go with bounded symbols and typed dependencies.
- Added project discovery, ownership and architecture evidence, incremental
  freshness, deterministic search, optional local semantic retrieval, change
  impact, test selection, and role-specific context planning.
- Integrated intelligence with runtime planning, review, control APIs, VS Code,
  replay drift, evaluation, and schema migrations without making it an
  authorization source.

## [0.4.0] - Replay and provenance milestone

- Added hierarchical execution tracing, sanitized content-addressed objects,
  tamper-evident provenance, checkpoints, and conservative replayability.
- Added providerless offline replay, isolated forks, structural comparisons,
  portable trace archives, and regression fixtures.
- Added cross-platform nondeterminism classification and explicit limits for
  uncaptured external systems.

## [0.3.0] - Managed tool runtime milestone

- Added the versioned `agentbus.tool` protocol, local capability derivation,
  deterministic policy, exact approvals, resource budgets, and immutable audit.
- Added contained filesystem operations, supervised subprocesses, policy-aware
  Git, managed MCP clients, and a constrained local AgentBus MCP server.
- Added cancellation and durable recovery across tool lifecycle states.

## [0.2.1] - Real local execution milestone

- Replaced simulated control-plane execution with real durable AgentBus runs.
- Added cooperative cancellation through providers, workers, tools, and local
  subprocess trees.
- Added real run, task, diff, report, and restart recovery flows in the native
  extension.

## [0.2.0] - Local control plane milestone

- Added an authenticated loopback FastAPI control plane, replayable SSE events,
  daemon discovery, Workspace Trust integration, and SecretStorage tokens.
- Added the native VS Code run, task, approval, diff, and report experience.
- Added protocol generation and true-offline control-plane acceptance.

## [0.1.0-alpha.1] - Initial durable runner

- Added installable `agentbus` and `agentbus-eval` entry points.
- Added Ollama-first routing, optional Azure OpenAI support, planner, coder,
  verifier, reviewer, durable task graphs, SQLite recovery, and approvals.
- Added bounded parallel worktrees with leases, fencing, task commits,
  deterministic integration, and conflict reporting.
- Added exact target-repository boundaries, generated-artifact hygiene,
  deterministic evaluation, regression baselines, and safe initialization.

## Known beta limitations

- There is no production SLA or distributed multi-tenant scheduler.
- Filesystem and external side effects are not transactionally rolled back.
- Worktrees and subprocess controls are defense in depth, not complete sandbox
  isolation.
- Replay cannot exactly reproduce uncaptured external systems.
- Repository intelligence is conservative, advisory, and may be partial.
- Live provider behavior, latency, quota, and cost depend on user configuration.
