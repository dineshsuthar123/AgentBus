# AgentBus public beta release checklist

Use this checklist from a reviewed release branch at the intended commit. Record
new evidence for every candidate; do not infer success from an earlier run.
These commands verify readiness but never publish, tag, push, merge, or contact a
live model provider.

## Candidate identity

- [ ] Confirm the version in `agentbus/_version.py`, `CHANGELOG.md`, Python
  package metadata, VS Code metadata, and compatibility ranges.
- [ ] Review the branch, commit history, complete diff, and migration impact.
- [ ] Confirm `git status --short` contains no unintended tracked change.
- [ ] Inspect staged and tracked files for credentials, private source, personal
  paths, databases, logs, indexes, traces, bundles, worktrees, VSIX files,
  dependencies, caches, bytecode, and benchmark output.
- [ ] Confirm Azure, Ollama, and public MCP access remain disabled for normal
  release acceptance.

## Python verification

- [ ] Run `python -m pytest`.
- [ ] Run `python -m compileall agentbus`.
- [ ] Run `python -m agentbus.control.acceptance`.
- [ ] Run `python -m agentbus.product_acceptance` on Windows and Ubuntu.
- [ ] Run `python -m agentbus.beta_acceptance`.
- [ ] Run `agentbus-eval run --suite release-offline --variant durable-parallel-fake`.
- [ ] Run `agentbus-eval run --suite repository-intelligence --variant deterministic`.
- [ ] Run `python -m agentbus.release_security`; inspect every tested boundary
  and unresolved limitation in the local defensive-security scorecard.
- [ ] Run `agentbus release-check --full` from a clean tracked worktree.
- [ ] Run `git diff --check`.

## Distribution verification

- [ ] Build wheel and source distribution twice with `python -m build --no-isolation`.
- [ ] Run `python -m agentbus.release_packaging` against both build sets and
  confirm semantic reproducibility.
- [ ] Inspect metadata, entry points, extras, package data, licenses, and archive
  paths.
- [ ] Confirm a fresh virtual environment imports AgentBus from the wheel, not
  the source checkout or an editable install.
- [ ] Confirm setup, doctor, daemon, indexing, deterministic task, final review,
  offline replay, cleanup, leak checks, and uninstall pass from the wheel.
- [ ] Confirm no package was uploaded to PyPI or another index.

## VS Code verification

From `extensions/vscode`:

- [ ] Run `npm ci`.
- [ ] Run `npm run protocol:check`.
- [ ] Run `npm run compile`.
- [ ] Run `npm run lint`.
- [ ] Run `npm test`.
- [ ] Run `npm run test:integration` under `xvfb-run` on Linux.
- [ ] Run `npm run test:product` under `xvfb-run` on Linux.
- [ ] Run `npm run package` and `npm run package:audit`.
- [ ] Inspect the VSIX inventory and confirm no source checkout, dependency,
  profile, credential, database, log, or runtime artifact is included.
- [ ] Confirm no VSIX was uploaded to a marketplace.

## Product evidence

- [ ] Review doctor statuses and any warnings rather than suppressing them.
- [ ] Review benchmark environment, operation samples, broad budgets, memory,
  and database-size evidence.
- [ ] Review bounded soak results for process, worktree, lease, event, cleanup,
  and memory leaks.
- [ ] Review support-bundle entries and redaction evidence without attaching
  private source-derived data by default.
- [ ] Confirm cleanup touched only marker-owned, inactive AgentBus state and did
  not reset or roll back repository files.
- [ ] Confirm failed-run documentation still states that source edits remain for
  inspection and manual cleanup.

## Optional live verification

- [ ] Treat manual Azure smoke as a separate, explicitly approved action.
- [ ] Supply secrets only through the protected CI environment and never print
  or persist their values.
- [ ] Use strict request, token, time, and repetition budgets.
- [ ] Confirm fallback, commit, push, PR creation, and publication remain
  disabled.
- [ ] Do not block an otherwise valid offline beta candidate solely because an
  optional paid-provider smoke was not authorized.

## Publication hold

- [ ] Generate reviewed Markdown and JSON evidence; leave missing checks marked
  `NOT_RUN` rather than guessing.
- [ ] Review known beta limitations, security guidance, support policy, and
  changelog wording.
- [ ] Obtain explicit maintainer approval before any tag, push, merge, package
  upload, marketplace upload, GitHub release, or announcement.
- [ ] Recheck the exact candidate commit and tracked worktree immediately before
  a separately authorized publication process.
- [ ] Never publish from an unreviewed, dirty, or artifact-contaminated worktree.
