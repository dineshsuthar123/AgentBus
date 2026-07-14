# Changelog

All notable changes are documented here. AgentBus follows semantic versioning;
the Python package uses PEP 440 prerelease spelling (`0.1.0a1`) for the release
named `0.1.0-alpha.1`.

## [0.1.0-alpha.1] - 2026-07-14

### Added

- Installable Python package with `agentbus` and `agentbus-eval` entry points.
- Ollama-first local routing and optional Azure OpenAI v1 provider support.
- Planner, Coder, Verifier, Reviewer, durable task graphs, approval gates, and
  SQLite recovery state.
- Bounded parallel execution in isolated Git worktrees with leases, fencing,
  task commits, deterministic integration, and conflict reporting.
- Generated-artifact filtering and target-repository boundary validation.
- Deterministic offline evaluation, regression baselines, repeated-run sample
  statistics, neutral variant comparisons, and explicit live-provider budgets.
- Safe `init`, offline `doctor`, configuration diagnostics, and release report
  commands.
- Opt-in, exact-SHA, license-reviewed real-repository benchmark manifests.

### Known limitations

- Execution is local and foreground; it does not provide distributed
  exactly-once semantics or a production service-level agreement.
- Filesystem and external side effects are not transactionally rolled back.
- Git worktrees reduce interference but are not complete sandbox isolation.
- Azure quality, capabilities, latency, quota, and cost depend on the selected
  deployment. Live checks are never automatic.
