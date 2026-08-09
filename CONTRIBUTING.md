# Contributing to AgentBus

AgentBus development is offline-first. Contributors do not need Azure, Ollama,
credentials, public MCP servers, or access to a private repository.

## Development setup

PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev,ide]"
.venv\Scripts\python.exe -m pytest
```

POSIX shell:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev,ide]'
.venv/bin/python -m pytest
```

Use the deterministic provider for complete local execution and acceptance.
Normal tests must not contact Azure, Ollama, public MCP, package indexes, or
other remote services after dependencies are installed.

## Architecture map

- `agentbus/agents` contains planner, coder, and reviewer boundaries.
- `agentbus/runtime` composes workflows, verification, context, and finalization.
- `agentbus/execution` contains durable state, scheduling, workers, leases, and
  integration.
- `agentbus/tools` and `agentbus/policy` implement managed tool protocol,
  capabilities, adapters, approvals, resources, and auditing.
- `agentbus/control` provides the local authenticated API and acceptance flow.
- `agentbus/trace` and `agentbus/replay` implement evidence and replay.
- `agentbus/intelligence` contains static parsing, indexing, graph, retrieval,
  impact, and context planning.
- `agentbus/product` contains setup, diagnostics, migrations, cleanup, support,
  benchmarks, soak, and release readiness.
- `extensions/vscode` is the native TypeScript client.
- `protocol` contains generated cross-language control artifacts.

Read [the ADR index](docs/architecture/README.md) before changing invariants.

## Test commands

Before every Python commit:

```powershell
.venv\Scripts\python.exe -m pytest <relevant-test-files> -vv
.venv\Scripts\python.exe -m compileall agentbus
git diff --check
```

Before final review:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m agentbus.eval run --suite core-offline --variant durable-parallel-fake
.venv\Scripts\python.exe -m agentbus.control.acceptance
```

Useful focused areas include `tests/test_workspace_scope.py`,
`tests/test_tool_*.py`, `tests/test_replay_*.py`,
`tests/test_intelligence_*.py`, and `tests/test_product_*.py`.

For the extension:

```powershell
cd extensions\vscode
npm ci
npm run protocol:check
npm run compile
npm run lint
npm test
npm run test:integration
npm run package
npm run package:audit
```

Electron tests require a graphical session or `xvfb-run` on Linux. The
fresh-profile product test must remain isolated and providerless.

## Adding a provider

Implement the central model-provider contract and normalized errors, usage,
cancellation, structured output, and retry behavior. Add deterministic adapter
tests. Never let provider output authorize tools or become durable workflow
truth. Live tests must be explicit, budgeted, and optional.

## Adding a tool

Define a versioned schema, local capability requirements, policy behavior,
resource accounting, cancellation, output bounds, redaction, immutable audit,
and recovery semantics. Test allow, constrained, approval, denial, timeout,
cancellation, malformed input, and containment paths. Keep `shell=False`.

## Adding a repository parser

Parsers must be static, deterministic, bounded, and partial-result aware. Do not
import repository code, execute build tools, invoke package managers, or fetch
dependencies. Add symbols, references, framework fixtures, malformed-input
tests, and incremental invalidation coverage.

## Protocol changes

Control models are the source of generated JSON Schema and TypeScript types.
After a compatible change:

```powershell
.venv\Scripts\python.exe -m agentbus.cli control-schema export
cd extensions\vscode
npm run protocol:check
```

Do not hand-edit generated artifacts. Breaking protocol or schema changes need
a version change, compatibility handling, migration plan, tests, and docs.

## Pull requests and commits

- Use focused Conventional Commits such as `feat(scope): ...`, `fix(scope): ...`,
  `test(scope): ...`, `docs: ...`, or `ci: ...`.
- Explain behavior, safety impact, migration impact, and verification evidence.
- Add tests for success, failure, recovery, and security boundaries.
- Preserve user changes; never add automatic reset, clean, or destructive
  rollback.
- Never commit `.env`, credentials, registries, SQLite databases, indexes,
  traces, support bundles, logs, benchmark output, worktrees, VSIX files,
  `node_modules`, virtual environments, caches, or private source.
- Do not weaken workspace boundaries, approval binding, final review,
  redaction, explicit live-provider consent, or artifact audits.

See [SECURITY.md](SECURITY.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and
[the release checklist](RELEASE_CHECKLIST.md).
