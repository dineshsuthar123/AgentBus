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

Profiles are `python-calculator` and `cancellation-two-task`. Failure and
latency injection use `AGENTBUS_DETERMINISTIC_LATENCY_SECONDS`,
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
