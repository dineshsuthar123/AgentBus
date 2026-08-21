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
| `validate` | Validate repositories, fixtures, and release reliability offline |
| `benchmark` | Run generated offline performance checks |
| `soak` | Run bounded offline reliability checks |
| `release-check` | Run non-publishing beta gates |

`agentbus soak --profile quick` is the short development check.
`agentbus soak --profile release-candidate` selects the bounded 5-10 minute
release profile. `--duration`, `--runs`, `--parallelism`, and
`--repository-files` may override profile defaults for explicit manual runs;
the soak remains synthetic, local, providerless, and non-publishing.

`agentbus validate reliability` combines the generated repository corpus with
the bounded quick lifecycle soak and emits an explicit `PASS`,
`PASS_WITH_WARNINGS`, or `FAIL` scorecard. Repeat `--repository PATH` to add
real local repositories; AgentBus indexes them into temporary databases and
does not modify their source trees. `--runs`, `--duration`, `--parallelism`,
`--repository-files`, and `--seed` provide bounded deterministic overrides.
Use `--json` for structured output or `--output PATH` for an atomic JSON report.
The report lists concrete checks and observations rather than an opaque numeric
score, and it never uses a provider or the network.

`agentbus benchmark all --output BASELINE.json` writes an atomic reusable local
performance baseline. A later compatible run can add `--baseline BASELINE.json`
and `--comparison-output SCORECARD.json` to classify broad regressions,
improvements, and neutral changes across release performance metrics. Baseline
comparison remains generated, providerless, offline, and deliberately tolerant
of ordinary CI timing variance.

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
