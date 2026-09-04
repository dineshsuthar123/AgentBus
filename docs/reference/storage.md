# Local storage

Use `syndra config paths --workspace . --json` to inspect resolved locations.
Paths below are defaults and can be changed by documented configuration.

| Data | Default | Notes |
| --- | --- | --- |
| User config | `%APPDATA%\Syndra\config.toml` or `~/.config/syndra/config.toml` | No credentials |
| Workspace config | `<repo>/.syndra/config.toml` | Must stay inside the workspace |
| State database | `<state_dir>/state.db` | Durable runs, attempts, approvals, leases, and metadata |
| Repository index | Beside the state database as `repository-index.sqlite3` | Local static metadata, not a source archive |
| Trace objects | Beside the state database in `trace-objects/` | Sanitized content-addressed evidence |
| Run records | `<workspace>/.syndra/runs/` | Bounded JSONL logs; an explicit absolute external `runs_dir` is supported |
| Logs | Setup-managed `logs/` | Rotated and redacted |
| Worktrees | `<repo-parent>/.syndra-worktrees/<repo-name>` | Must be outside the source repository |
| Daemon registry | Platform-specific local registry path | Metadata only; bearer token stays in secure storage/handshake |

Failed runs may leave source edits because automatic destructive rollback is
forbidden. Reports list created and modified files even on failure.

Relative `state_dir` values resolve from the canonical workspace. Relative
`runs_dir` values resolve inside that state directory. Syndra rejects a run
log directory inside the repository unless it is under `<workspace>/.syndra`;
use an absolute path to configure external runtime storage.

Existing `.agentbus` state remains readable when `.syndra` is absent. Syndra
does not automatically move or rewrite legacy databases, runs, or traces.

Start cleanup with a dry run:

```console
syndra cleanup --dry-run --stale --json
syndra worktrees list
```

`--all-runtime-state` requires explicit confirmation and still removes only
validated Syndra-owned runtime data. It does not uninstall Python, delete
user repositories, reset Git, or remove source edits.
