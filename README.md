# AgentBus

AgentBus is a safety-oriented local runtime for software-engineering agents. It
plans repository tasks, executes versioned workspace-scoped tools, verifies the
result, requires a final review, records durable evidence, and supports offline
replay. Use it from the CLI or the native VS Code extension with a built-in
deterministic provider, local Ollama, or an explicitly configured Azure OpenAI
deployment.

AgentBus `0.6.0b1` is a public beta. It is suitable for controlled local
evaluation and offline CI, not an unattended production service. It does not
provide complete sandbox isolation or automatically roll back file edits.

## Install

AgentBus supports Python 3.11 through 3.14 on the tested Windows and Linux
paths. From a source checkout:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[ide]"
.venv\Scripts\agentbus.exe version --json
```

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[ide]'
.venv/bin/agentbus version --json
```

The core package includes the CLI, deterministic and Ollama providers, durable
runtime, managed tools, replay, and repository intelligence. Add `ide` for the
control plane and VS Code, `azure` for Azure OpenAI, `mcp` for HTTP MCP, or
`all` for all runtime integrations. AgentBus never installs Git, Ollama, model
weights, VS Code, or system packages.

[Installation guide](docs/getting-started/install.md)

## Five-Minute Quickstart

Prove the full local workflow without credentials or network access:

```console
agentbus quickstart --json
```

The quickstart creates a temporary Git repository, builds a local index, runs
the deterministic planner and managed tools, executes tests, verifies and
reviews the change, reports artifacts, and removes its owned temporary state.

For a disposable repository of your own:

```console
agentbus setup --workspace . --provider deterministic --scope workspace --non-interactive --dry-run
agentbus setup --workspace . --provider deterministic --scope workspace --non-interactive
agentbus doctor --workspace . --provider deterministic --json
agentbus index build --workspace . --json
agentbus run --workspace . --provider deterministic --workflow multi --durable "Create and verify a small calculator"
agentbus runs
```

[Quickstart guide](docs/getting-started/quickstart.md) | [VS Code onboarding](docs/getting-started/vscode.md)

## Why AgentBus

- Durable SQLite-backed task graphs can resume without rerunning terminal
  successful tasks.
- Planner, coder, verifier, and final reviewer have distinct responsibilities.
- Versioned managed tools use independently derived capabilities, deterministic
  policy, exact approvals, and cumulative resource budgets.
- Workspace and Git boundaries reject accidental parent-repository scope.
- Parallel tasks use isolated, fenced Git worktrees and deterministic
  integration.
- Local repository intelligence adds symbols, dependencies, ownership,
  architecture evidence, impact analysis, tests, and context plans.
- Sanitized traces, provenance, offline replay, comparisons, and regression
  fixtures make failures inspectable.
- Offline evaluation, synthetic benchmarks, soak checks, package audits, and
  release gates require no live provider.
- The authenticated loopback control plane and native VS Code extension expose
  runs, tools, approvals, diffs, reports, replay, and index state.

## Architecture

```text
CLI / VS Code
     |
     v
local authenticated control plane or direct runtime
     |
     +--> planner --> durable task graph --> coder
     |                                   |
     |                                   v
     |                          managed tool runtime
     |                         /        |         \
     |                  filesystem   process     Git/MCP
     |                         \        |         /
     |                          canonical workspace
     |
     +--> verifier --> mandatory final reviewer --> optional commit / PR
     |
     +--> SQLite state + JSONL events + trace objects + repository index
     |
     +--> deterministic / Ollama / Azure OpenAI provider adapter
```

Provider output is never authorization. Every tool operation is validated by
local code, and reviewer rejection prevents commit and pull-request creation.

[Architecture decisions](docs/architecture/README.md)

## Providers

| Provider | Intended use | Network | Credentials |
| --- | --- | --- | --- |
| `deterministic` | Quickstart, tests, CI, demos, failure injection | None | None |
| `ollama` | Local model execution | Configured local URL | None by default |
| `azure` | Explicit Azure OpenAI deployment | Azure endpoint | Required via environment/secure store |

Provider checks are local unless `--live` is supplied. AgentBus never silently
switches to a live provider, and normal CI does not require Azure or Ollama.

[Provider guide](docs/guides/providers.md) | [Azure OpenAI](docs/providers/azure-openai.md)

## Supported Repositories

Managed filesystem, process, test, and Git tools operate on text repositories
within their validated capability scope. The local repository-intelligence
parsers understand Python, TypeScript/JavaScript, Java, and Go. Unknown file
types remain available to bounded scanning but do not receive invented symbol
or dependency claims.

Indexing is static and local: AgentBus does not import repository modules,
execute setup files, invoke builds or package managers, or upload source.

[Repository-intelligence guide](docs/guides/repository-intelligence.md)

## Safety Model

AgentBus applies defense in depth:

- canonical workspace and exact Git top-level validation;
- path traversal, credential path, unsafe link, Windows device, and alternate
  data-stream rejection;
- absolute executable identity, separate arguments, `shell=False`, sanitized
  environment, bounded output, timeout, and process-tree cleanup;
- explicit MCP configuration and exact capability maps;
- revision-bound approval for risky operations;
- bounded redacted logs, reports, support bundles, traces, and errors;
- mandatory verification and final review before optional publication;
- no automatic reset, clean, destructive rollback, push, or PR by default.

These controls are not a VM, container, firewall, restricted OS token, seccomp
profile, or proof that generated code is safe. Run AgentBus with least-privilege
OS and provider credentials, use disposable repositories while evaluating it,
and review diffs before commit or execution.

[Security overview](docs/security/README.md) | [Security policy](SECURITY.md)

## Common Workflows

```console
# Inspect effective configuration without revealing credentials
agentbus config show --workspace . --json

# Query local code evidence
agentbus search calculate_total --workspace . --evidence
agentbus impact src/calculator.py --workspace .
agentbus tests-for src/calculator.py --workspace .

# Inspect and replay a run without providers
agentbus show-run <run-id>
agentbus replay <run-id> --mode offline --json

# Preview safe cleanup
agentbus cleanup --dry-run --stale --json

# Run local non-publishing beta gates
agentbus release-check
```

[Practical workflows](docs/guides/workflows.md) | [CLI reference](docs/reference/cli.md)

## Current Limitations

- Public beta compatibility is guaranteed only within the documented `0.6`
  minor line; pre-v1 minor releases may make documented breaking changes.
- Filesystem and external side effects are not transactional. Failed runs may
  leave files for inspection, and reports list those artifacts.
- Worktrees reduce task interference but are not complete process isolation.
- Offline replay can reproduce only captured, sanitized, controlled evidence;
  uncaptured external systems are reported as partial or non-replayable.
- Repository intelligence is conservative and may be partial or stale. It is
  advisory, not authorization or perfect code understanding.
- Azure quality, capability, latency, quota, and cost depend on the selected
  deployment. Live checks are explicit and may incur cost.
- There is no production SLA, distributed scheduler, remote multi-tenant
  service, or unattended auto-merge path.

[Compatibility policy](docs/reference/compatibility.md) | [Changelog](CHANGELOG.md)

## Documentation

- [Documentation home](docs/README.md)
- [Configuration](docs/reference/configuration.md)
- [Tools and approvals](docs/guides/tools-and-approvals.md)
- [Replay](docs/guides/replay.md)
- [MCP](docs/guides/mcp.md)
- [Troubleshooting](docs/troubleshooting/install.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)

## Development

The deterministic provider supports a fully offline contributor loop:

```console
python -m pip install -e ".[dev,ide]"
python -m pytest
python -m compileall agentbus
python -m agentbus.eval run --suite core-offline --variant durable-parallel-fake
git diff --check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for focused Python, protocol, control
plane, repository-intelligence, and VS Code commands.

## License

AgentBus is licensed under the [MIT License](LICENSE).
