# Configuration reference

## Precedence

AgentBus resolves settings in this deterministic order, from lowest to highest:

1. built-in defaults;
2. user config;
3. workspace `.agentbus/config.toml`;
4. explicit `--config` file;
5. CLI overrides;
6. environment variables.

AgentBus does not search parent directories and does not load `.env` files.
Workspace configuration cannot redirect execution outside its workspace.

Inspect values and their sources safely:

```console
agentbus config paths --workspace . --json
agentbus config show --workspace . --json
agentbus config explain provider_name --workspace . --json
agentbus config validate --workspace . --json
```

Sensitive values are represented only as configured/not configured.

## Locations

The user config is `%APPDATA%\AgentBus\config.toml` on Windows and
`${XDG_CONFIG_HOME:-~/.config}/agentbus/config.toml` on Linux. The workspace
config is `<workspace>/.agentbus/config.toml`.

## Safe mutation

```console
agentbus config set provider_name deterministic --scope workspace --workspace .
agentbus config unset provider_name --scope workspace --workspace .
```

Mutation validates the complete replacement document and writes atomically.
AgentBus refuses configuration symlinks and rejects credential-shaped keys.
Secrets must remain in the process environment or a secure store.

## Common fields

| Field | Purpose |
| --- | --- |
| `workspace_dir` | Exact target repository root |
| `provider_name` | `deterministic`, `ollama`, or `azure` |
| `durable_execution` | Persist resumable task graphs |
| `parallel_execution` | Use isolated worktrees for independent tasks |
| `max_workers` | Bound local parallel workers |
| `repository_intelligence` | Enable local index guidance |
| `semantic_retrieval` | Explicit opt-in local semantic retrieval |
| `worktree_root` | Owned worktree root outside the source repository |
| `keep_worktrees` | Retain clean owned worktrees for diagnostics |
| `policy_mode` | Public beta requires `enforce` |
| `trace_retention_days` | Trace retention horizon |
| `log_level` | `error`, `warning`, `info`, `debug`, or `trace` |

Use `agentbus config show --json` for the complete current field set and
[environment variables](environment-variables.md) for overrides.
