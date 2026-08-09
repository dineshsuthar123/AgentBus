# CLI reference

Run `agentbus --help` for the current command list and
`agentbus <command> --help` for exact options.

## Product lifecycle

| Command | Purpose |
| --- | --- |
| `version` | Package, Python, protocol, schema, and extension compatibility |
| `setup` | Offline-first user or workspace setup |
| `quickstart` | Disposable deterministic first task |
| `doctor` | Offline diagnostics; live provider checks are explicit |
| `upgrade-check` | Package, config, schema, and extension compatibility |
| `migrate` | Status, plan, verify, or apply local migrations |
| `cleanup` | Dry-run or remove proven owned stale runtime state |
| `logs` | Read bounded redacted product/run logs |
| `support-bundle` | Create a sanitized local diagnostic ZIP |
| `benchmark` | Run generated offline performance checks |
| `soak` | Run bounded offline reliability checks |
| `release-check` | Run non-publishing beta gates |

## Execution

| Command | Purpose |
| --- | --- |
| `run` | Start a task |
| `resume` | Resume a nonterminal durable run |
| `runs` | List durable runs |
| `show-run` | Show status, failures, review, workspace, and artifacts |
| `approve` / `reject` | Resolve an exact pending task approval |
| `worktrees` | Inspect or explicitly clean owned worktrees |

`--commit` is opt-in. `--open-pr` additionally requires a successful commit,
final review, a configured remote, and `gh`. Reviewer rejection prevents both.

## Providers and daemon

`providers` lists, explains, or checks configured routes. A network request
requires `providers check <name> --live`. `serve` starts one authenticated
loopback daemon; `daemon` manages discovery, status, restart, stop, stale
registry cleanup, and logs.

## Evidence and replay

`trace`, `replay`, and `compare` inspect, verify, export, import, replay, fork,
and compare sanitized execution evidence. Offline replay never uses a provider.

## Repository intelligence

`index`, `search`, `symbols`, `dependencies`, `dependents`, `impact`,
`tests-for`, and `context-plan` query the local static index.

## Evaluation

`agentbus evaluate` routes to the evaluation harness. The separate
`agentbus-eval` entry point also provides `list`, `run`, `show`, `compare`,
baseline, and sanitized export commands.
