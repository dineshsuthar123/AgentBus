# Local storage

Use `agentbus config paths --workspace . --json` to inspect resolved locations.
Paths below are defaults and can be changed by documented configuration.

| Data | Default | Notes |
| --- | --- | --- |
| User config | `%APPDATA%\AgentBus\config.toml` or `~/.config/agentbus/config.toml` | No credentials |
| Workspace config | `<repo>/.agentbus/config.toml` | Must stay inside the workspace |
| State database | `<state_dir>/state.db` | Durable runs, attempts, approvals, leases, and metadata |
| Repository index | Beside the state database as `repository-index.sqlite3` | Local static metadata, not a source archive |
| Trace objects | Beside the state database in `trace-objects/` | Sanitized content-addressed evidence |
| Run records | `runs/` unless configured | Bounded JSONL and reports |
| Logs | Setup-managed `logs/` | Rotated and redacted |
| Worktrees | `<repo-parent>/.agentbus-worktrees/<repo-name>` | Must be outside the source repository |
| Daemon registry | Platform-specific local registry path | Metadata only; bearer token stays in secure storage/handshake |

Failed runs may leave source edits because automatic destructive rollback is
forbidden. Reports list created and modified files even on failure.

Start cleanup with a dry run:

```console
agentbus cleanup --dry-run --stale --json
agentbus worktrees list
```

`--all-runtime-state` requires explicit confirmation and still removes only
validated AgentBus-owned runtime data. It does not uninstall Python, delete
user repositories, reset Git, or remove source edits.
