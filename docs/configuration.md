# Configuration

AgentBus resolves configuration in this order, from highest to lowest:

1. CLI overrides.
2. Process environment.
3. An explicitly named TOML or JSON config file.
4. Safe built-in defaults.

AgentBus does not search parent directories for `.env` files and does not load
dotenv files automatically. Use `--config PATH` explicitly. Real `.env` files
must remain ignored; `.env.example` contains placeholders only.

## Config files

```toml
[agentbus]
provider_name = "ollama"
workspace_dir = "C:\\projects\\sample"
state_dir = "C:\\agentbus-state"
state_db = "state.db"
runs_dir = "C:\\agentbus-state\\runs"
tool_resource_budget = { wall_clock_seconds = 60, invocations_per_task = 32, invocations_per_run = 256 }
```

JSON may contain the same keys either at the root or below `agentbus`. Unknown
keys are rejected. Relative paths resolve from the current process directory;
use absolute paths for automation.

```powershell
agentbus config show --config .agentbus\config.toml
agentbus config validate --config .agentbus\config.toml
agentbus config paths --config .agentbus\config.toml --json
```

`config show` reports each non-secret value and its source. Secret fields show
only whether a value is configured. Azure endpoints are reduced to their host.

## Core environment variables

| Variable | Purpose |
| --- | --- |
| `AGENTBUS_WORKSPACE` | Exact target Git repository root |
| `AGENTBUS_PROVIDER` | `ollama`, `azure`, or `deterministic` |
| `AGENTBUS_MODEL` | Ollama model or active-provider CLI default |
| `AGENTBUS_OLLAMA_URL` | Ollama HTTP endpoint |
| `AGENTBUS_STATE_DIR` / `AGENTBUS_STATE_DB` | Durable SQLite location |
| `AGENTBUS_RUNS_DIR` | JSONL audit log directory |
| `AGENTBUS_PARALLEL_EXECUTION` | Explicit bounded parallel mode |
| `AGENTBUS_MAX_WORKERS` | Local worker limit |
| `AGENTBUS_WORKTREE_ROOT` | AgentBus-owned worktrees outside the repository |
| `AGENTBUS_ENABLE_PROVIDER_FALLBACK` | Explicit Azure-to-Ollama fallback |

Role model variables are `AGENTBUS_PLANNER_MODEL`, `AGENTBUS_CODER_MODEL`,
`AGENTBUS_REVIEWER_MODEL`, and `AGENTBUS_SUMMARIZER_MODEL`.

## Tool resource budget

`tool_resource_budget` is a validated JSON or TOML object. Omitted fields use
the secure protocol defaults. The complete fields are:

| Field | Default |
| --- | ---: |
| `wall_clock_seconds` | 90 |
| `stdout_bytes` | 65,536 |
| `stderr_bytes` | 65,536 |
| `combined_output_bytes` | 131,072 |
| `artifact_bytes` | 5,242,880 |
| `child_processes` | 8 |
| `concurrent_processes` | 2 |
| `invocations_per_task` | 64 |
| `invocations_per_run` | 512 |
| `file_mutations` | 100 |
| `total_written_bytes` | 10,485,760 |
| `maximum_file_bytes` | 2,097,152 |
| `memory_bytes` | unset |
| `cpu_seconds` | unset |

The loader rejects unknown fields, inconsistent output limits, a per-task
invocation limit above the run limit, and invalid numeric ranges. Budgets
accumulate across retries and duplicate invocation IDs and may only tighten
during a run. Platform reports distinguish requested, supported, enforced, and
observed values. See [Managed Tool Runtime](tool-runtime.md) and
[Sandbox Security](sandbox-security.md).

## MCP servers

`mcp_server_configs` is an explicit list available only in a named JSON or TOML
configuration file or typed Python configuration. It is not populated by
automatic discovery or a broad environment variable. Each entry must select
`stdio` or explicitly authenticated `loopback_http`, declare a unique server
ID, and map every imported tool to exact capabilities. Safe configuration
output shows only server ID, transport, and configured tool names.

Stdio commands use allowlisted executable aliases and sanitized environments.
HTTP endpoints require numeric loopback, explicit opt-in, and a bearer token.
If a config file contains that token, protect the file and keep it out of
source control. See [MCP Integration](mcp-integration.md) for complete schemas
and examples.

## Deterministic provider

The `deterministic` provider is network-free and uses the same provider
contract, routing, parsing, usage accounting, cancellation, and durable runtime
as Ollama and Azure. It is intended for development, demos, CI, and acceptance,
not general code generation.

```powershell
$env:AGENTBUS_PROVIDER = "deterministic"
$env:AGENTBUS_DETERMINISTIC_PROFILE = "python-calculator"
python -m agentbus.main --provider deterministic --workflow multi --durable `
  --parallel --max-workers 1 --workspace C:\path\to\isolated-repo `
  "Create and verify the deterministic calculator"
```

Profiles include `python-calculator`, `cancellation-two-task`,
`tool-safe-read`, `tool-atomic-write`, `tool-source-patch`, `tool-pytest`,
`tool-git-diff`, `tool-git-commit`, `tool-delete-approval`,
`tool-deny-outside-read`, `tool-deny-credential-read`,
`tool-process-timeout`, `tool-process-cancel`, `tool-excessive-output`,
`tool-budget-exhaustion`, `tool-local-mcp`, `tool-loop-limit`, and
`tool-control-acceptance`. They exercise the real managed runtime without a
provider or public MCP call. Failure and latency injection use
`AGENTBUS_DETERMINISTIC_LATENCY_SECONDS`,
`AGENTBUS_DETERMINISTIC_LATENCY_ROLES`,
`AGENTBUS_DETERMINISTIC_FAILURE_KIND`,
`AGENTBUS_DETERMINISTIC_FAILURE_CALLS`, and
`AGENTBUS_DETERMINISTIC_FAILURE_ROLES`.

## Azure

```powershell
$env:AGENTBUS_PROVIDER = "azure"
$env:AZURE_OPENAI_ENDPOINT = "https://YOUR-RESOURCE.openai.azure.com"
$env:AZURE_OPENAI_API_KEY = "..."
$env:AZURE_OPENAI_DEFAULT_DEPLOYMENT = "your-deployment"
```

Role deployment variables use `AZURE_OPENAI_<ROLE>_DEPLOYMENT`. API-key
authentication is implemented in this alpha; the `entra` extra reserves
dependencies for future Entra support but does not enable it. Diagnostics never
print the key. Endpoint normalization permits only the resource root or
`/openai/v1/` path and strips no hidden query credentials.

## Evaluation

Evaluation uses `AGENTBUS_EVAL_RESULTS_DIR`,
`AGENTBUS_EVAL_FIXTURE_ROOT`, `AGENTBUS_EVAL_PRESERVE_FIXTURES`,
`AGENTBUS_EVAL_MAX_REQUESTS`, `AGENTBUS_EVAL_MAX_TOKENS`, and
`AGENTBUS_EVAL_TIMEOUT_SECONDS`. Live access still requires `--live`; an
environment value cannot silently opt in.

## Repository intelligence

Repository intelligence uses the resolved `workspace_dir` and stores
`repository-index.sqlite3` beside the resolved AgentBus state database. The
database is local runtime state and must not be committed. CLI commands accept
explicit `--workspace`, `--index-db`, and portable `--repository-key` overrides:

```powershell
agentbus index build --config .agentbus\config.toml
agentbus index status --workspace C:\src\sample --json
agentbus search calculate_total --workspace C:\src\sample
```

An explicit workspace always wins for that command and is canonically contained
before indexing. Runtime and replay resolve the index beside the selected run's
state and exact workspace; they do not fall back to an index for a daemon default
or parent repository.

There are no Azure, Ollama, embedding, or network settings required for normal
indexing. Optional semantic retrieval is a Python integration API, disabled by
default. It requires an explicit local provider descriptor that declares source
is not sent off-device. AgentBus does not download semantic models or silently
enable remote embeddings.

See [Repository Intelligence](repository-intelligence.md) and
[Incremental Indexing](incremental-indexing.md).
