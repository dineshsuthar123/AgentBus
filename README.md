# AgentBus

## VS Code Control Plane

Install the optional local IDE stack and start a loopback-only daemon:

```powershell
pip install "agentbus[ide]"
agentbus serve --port 0 --json-ready
```

The native extension in `extensions/vscode` discovers or starts the daemon,
stores its bearer token only in VS Code SecretStorage, respects Workspace Trust,
streams replayable progress, and opens changes with native diff editors. See
[`docs/control-plane.md`](docs/control-plane.md),
[`docs/daemon-security.md`](docs/daemon-security.md), and
[`docs/vscode-extension.md`](docs/vscode-extension.md).

AgentBus is a provider-independent local coding runtime for small software engineering tasks. It can use local Ollama models or Azure OpenAI deployments for structured agent actions, executes a small set of workspace-scoped tools, records machine-readable run state, and stops when the work is finished or the configured step limit is reached. Ollama remains the default and requires no Azure configuration.

AgentBus `0.1.0-alpha.1` is an early developer release. It has no production
SLA, does not provide complete sandbox isolation, and does not automatically
roll back filesystem or external side effects.

## Quick Start

Install from a source checkout with standard Python packaging:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\agentbus.exe --version
.venv\Scripts\agentbus.exe init --local --provider ollama --dry-run
```

```bash
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/agentbus --version
.venv/bin/agentbus init --local --provider ollama --dry-run
```

Remove `--dry-run` when the reported paths are correct, then run offline
diagnostics and a task from the exact root of a disposable Git repository:

```powershell
agentbus init --local --provider ollama
agentbus doctor --config .agentbus\config.toml
agentbus run --config .agentbus\config.toml --workflow multi --durable "Add a tested feature"
agentbus runs --config .agentbus\config.toml
agentbus-eval run --suite release-offline --variant durable-parallel-fake
```

AgentBus never downloads an Ollama model. Install Ollama and obtain the desired
model explicitly:

```bash
ollama pull qwen2.5-coder:7b
```

Start with [Getting Started](docs/getting-started.md), then see
[Configuration](docs/configuration.md), the
[Python calculator example](examples/python-calculator/README.md), and the
[Safety Model](#safety-model).

## Model Providers

AgentBus agents depend on a central model-provider contract rather than an SDK. The router resolves provider, role, model/deployment, timeout, provider-call retry policy, and optional fallback. Provider adapters return normalized values, request IDs, latency, finish status, and token usage where available. SDK objects and remote response IDs are never durable workflow truth.

Supported providers:

- `ollama` is the local/offline default and preserves `AGENTBUS_MODEL` plus `AGENTBUS_OLLAMA_URL` behavior.
- `azure` uses the official OpenAI Python SDK against the Azure OpenAI v1 base URL.

List providers and inspect redacted routing configuration without making a network request:

```bash
python -m agentbus.main --list-providers
python -m agentbus.main --show-model-config
python -m agentbus.main --check-provider ollama
python -m agentbus.main --check-provider azure
```

`--check-provider` is local-only unless `--live` is supplied explicitly. The first real Azure smoke test should be run only after credentials and deployments are configured:

```bash
python -m agentbus.main --check-provider azure --live
```

The live check sends one minimal structured request. It prints only provider, deployment, latency, request ID, and usage metadata; it does not start a task or modify workspace files.

### Azure OpenAI Setup

Create an Azure OpenAI resource and at least one compatible model deployment, then set environment variables in the process that launches AgentBus. AgentBus does not load `.env` automatically. A placeholder-only template is available in `.env.example`.

PowerShell example:

```powershell
$env:AGENTBUS_PROVIDER = "azure"
$env:AZURE_OPENAI_ENDPOINT = "https://YOUR-RESOURCE.openai.azure.com"
$env:AZURE_OPENAI_API_KEY = "YOUR-KEY"
$env:AZURE_OPENAI_AUTH_MODE = "api_key"
$env:AZURE_OPENAI_API_MODE = "responses"
$env:AZURE_OPENAI_DEFAULT_DEPLOYMENT = "YOUR-DEFAULT-DEPLOYMENT"
$env:AZURE_OPENAI_PLANNER_DEPLOYMENT = "YOUR-PLANNER-DEPLOYMENT"
$env:AZURE_OPENAI_CODER_DEPLOYMENT = "YOUR-CODER-DEPLOYMENT"
$env:AZURE_OPENAI_REVIEWER_DEPLOYMENT = "YOUR-REVIEWER-DEPLOYMENT"
```

The endpoint is normalized to `https://<resource>.openai.azure.com/openai/v1/`; do not append deployment paths, query-string API keys, or a dated `api-version`. The configured deployment name, not a public model family name, is sent as the API `model` argument.

Role resolution uses a role-specific deployment first and `AZURE_OPENAI_DEFAULT_DEPLOYMENT` second. Planner, coder, reviewer, summarizer, and default routes are independent. A missing role and default deployment produces a configuration error before a request is sent.

Responses API mode is the default. `AZURE_OPENAI_API_MODE=chat_completions` is an explicit alternative; AgentBus never silently switches modes. Not every Azure model or deployment supports every API or structured-output capability.

Planner plans, reviewer decisions, and tool actions supply Pydantic schemas. AgentBus requests schema-constrained output when supported and always validates the result locally again. Malformed output, missing required fields, invalid enum values, and forbidden extra fields fail closed. Dictionary JSON Schemas are also validated locally with `jsonschema`.

### Retry And Fallback

Provider-call retries are separate from durable task-attempt retries. Only normalized transient failures such as timeouts, connection failures, rate limits, and temporary service errors are retried. AgentBus honors bounded `Retry-After` values or uses capped exponential backoff with jitter. Authentication, authorization, configuration, deployment-not-found, content-policy, bad-request, and schema-validation errors are not provider-retried.

Fallback is disabled by default. The only supported automatic fallback path is Azure to Ollama, enabled explicitly with:

```powershell
$env:AGENTBUS_FALLBACK_PROVIDER = "ollama"
$env:AGENTBUS_ENABLE_PROVIDER_FALLBACK = "true"
```

Fallback occurs only after transient Azure retries are exhausted. It never occurs for authentication, authorization, missing configuration, deployment-not-found, invalid schema/request, content-policy, tool-safety, path, command, or approval failures. Fallback is logged explicitly and does not bypass verifier, reviewer, durable state, or human approval gates.

### Usage Metadata

When a provider exposes usage, AgentBus records input, output, total, and cached token counts together with provider, deployment, role, latency, request ID, retry count, and fallback provenance. Durable attempts store these safe fields as JSON metadata, avoiding a database schema migration. `UsageLedger` can aggregate by run, task, role, provider, and deployment. AgentBus does not estimate monetary cost because Azure pricing varies by deployment, model, region, and agreement.

See [Azure OpenAI Provider](docs/providers/azure-openai.md) for setup and troubleshooting and [ADR 0002](docs/adr/0002-model-provider-routing.md) for design rationale.

## Run Tests

```bash
python -m pytest
```

## Evaluation And Regression Harness

Unit tests establish component behavior, but they do not show whether the complete AgentBus runtime can finish a repository task with the right files, passing tests, bounded model usage, durable recovery, and a successful final review. The evaluation harness runs compact software-engineering cases through the existing agents, durable engine, scheduler, worktrees, verifier, and reviewer, then checks the result with deterministic assertions. A model review is recorded, but it is not the sole correctness oracle.

The default `core-offline` suite uses exact, route-aware fake provider responses and fresh local Git repositories. It requires no provider, credentials, or network access:

```powershell
.venv\Scripts\python.exe -m agentbus.eval list
.venv\Scripts\python.exe -m agentbus.eval run --suite core-offline
.venv\Scripts\python.exe -m agentbus.eval run --suite core-offline --variant durable-parallel-fake
```

Use `--case <case-id>` or `--tag <tag>` to filter cases, `--fail-fast` to stop after the first failed case, `--json` for machine-readable output, and `--preserve-fixtures` to retain disposable repositories for debugging. Result JSON is written separately from durable runtime state under `.agentbus/evaluations` by default. Failed evaluation runs persist completed case results incrementally.

### Assertions And Scoring

Cases can assert run, verifier, and reviewer outcomes; required, forbidden, and changed files; exact or pattern-based contents; test commands; generated-artifact exclusion; repository boundaries; commit and PR behavior; approval gates; task execution counts; conflicts; source immutability; secret patterns; and request, token, time, and retry limits. Diagnostics retain expected and observed values.

The default score totals 100 points:

| Dimension | Weight |
| --- | ---: |
| Functional correctness | 30 |
| Test success | 20 |
| Scope discipline | 15 |
| Safety compliance | 15 |
| Recovery and integration | 10 |
| Review outcome | 5 |
| Efficiency | 5 |

Each applicable dimension receives its weight multiplied by its assertion pass rate. Missing optional dimensions are neutral. Any hard safety assertion failure sets the score to zero and fails the case regardless of its other points. Raw quality, execution, provider, and Git metrics are stored alongside the score; AgentBus does not infer monetary cost from hardcoded prices.

### Baselines And Regression Gates

Save a passing run as a named baseline and compare later runs:

```powershell
.venv\Scripts\python.exe -m agentbus.eval baseline save <run-id> --name main
.venv\Scripts\python.exe -m agentbus.eval baseline compare <new-run-id> --name main
.venv\Scripts\python.exe -m agentbus.eval compare <baseline-run-id> <new-run-id>
.venv\Scripts\python.exe -m agentbus.eval show <run-id>
.venv\Scripts\python.exe -m agentbus.eval export <run-id> --output report.json
```

Replacing a baseline requires `--replace`. Comparison fails on configured regressions such as a previously passing or verifier-passing case failing, a safety violation, excessive score loss, more unrelated files, or token, latency, and retry growth beyond thresholds. Quality and safety transitions are critical; timing and usage thresholds are configurable because host and provider performance varies.

### Repeated Runs And Variant Reports

`--repeat` stores every immutable run plus one aggregate series. The series
reports success rate, score mean/median/minimum/sample standard deviation,
duration and token means/medians, retry distribution, fallback, reviewer,
verifier, file-scope violation, and conflict rates. These are descriptive
sample statistics and do not imply significance at small sample sizes.

```powershell
agentbus-eval run --suite release-offline --variant durable-parallel-fake --repeat 3
agentbus-eval show-series SERIES_ID
agentbus-eval compare-variants RUN_OR_SERIES_A RUN_OR_SERIES_B --format markdown --output comparison.md
```

Comparison output shows right-minus-left differences and never labels one
variant "best" from a single run.

### Live Evaluation Safety

Live Ollama and Azure variants are opt-in. A live run requires an explicitly selected live suite, a live variant, and `--live`; otherwise it fails before provider construction. The CLI prints the provider, role deployment summary, estimated maximum calls, and hard request/token limits before execution. Every provider call is locally reserved against request, conservative token, and wall-clock budgets before network access. Evaluation never pushes, opens a PR, or deploys infrastructure, and provider fallback occurs only when the selected variant enables it.

```powershell
$env:AGENTBUS_EVAL_MAX_REQUESTS = "8"
$env:AGENTBUS_EVAL_MAX_TOKENS = "2000"
$env:AGENTBUS_EVAL_TIMEOUT_SECONDS = "180"
.venv\Scripts\python.exe -m agentbus.eval run --suite azure-smoke --variant durable-azure --live
```

The live Azure suite is intentionally not part of normal tests and should be run only after deployment and credential configuration. `release-azure-smoke` contains one bounded fixture and is intended for explicit `--repeat 2` release checks. Evaluation result metadata is sanitized and excludes task prompts, API keys, environment dumps, source snapshots, and SDK objects. Configuration fingerprints include prompt templates, action schemas, routing, retry/fallback, and workflow settings without secrets.

Fixture repositories are copied into marker-owned temporary directories and initialized as independent Git repositories. Cleanup validates the exact ownership marker and never resets, cleans, or deletes an unknown repository. `AGENTBUS_EVAL_PRESERVE_FIXTURES=true` retains fixtures; `AGENTBUS_EVAL_RESULTS_DIR` and `AGENTBUS_EVAL_FIXTURE_ROOT` override storage locations.

License-reviewed real-repository benchmarks are available but require explicit
`--allow-repository-download`, an exact manifest SHA, a live variant, and
`--live`. They never run in normal tests. See
[Benchmarking Real Repositories](docs/benchmarking-real-repositories.md).

Current limitations: suite cases run sequentially, fixtures are deliberately compact, live results can vary by model and service conditions, and no pricing model is included. Passing these fixtures does not guarantee correctness, security, or reliability on arbitrary production repositories. See [ADR 0005](docs/adr/0005-evaluation-and-regression-harness.md) and [ADR 0006](docs/adr/0006-v0.1-release-and-real-repo-validation.md).

## Run The CLI

Installed command groups include `run`, `resume`, `runs`, `show-run`,
`approve`, `reject`, `providers`, `config`, `init`, `doctor`, `worktrees`,
`evaluate`, `release-report`, and `version`. The original
`python -m agentbus.main` option-oriented interface remains supported.

Single-agent mode is the default. It sends the task directly to one AgentBus tool-using loop.

Interactive single-agent mode:

```bash
python -m agentbus.main
```

Command-line task:

```bash
python -m agentbus.main "Create hello.py and run it"
```

Multi-agent mode runs a local Planner -> Coder -> Verifier -> Reviewer workflow:

```bash
python -m agentbus.main --workflow multi "Create calculator.py with tests"
```

Durable multi-agent mode persists a validated task graph before coding starts:

```bash
python -m agentbus.main --workflow multi --durable "Create calculator.py with tests"
```

Parallel durable mode is explicit and bounded. Each active task receives an isolated
Git worktree:

```bash
python -m agentbus.main --workflow multi --durable --parallel --max-workers 3 --workspace C:\path\to\repo "Implement independent backend and documentation tasks"
```

Print the local repo context pack without running an agent:

```bash
python -m agentbus.main --workspace workspace --show-context
```

Print context first, then continue into the selected workflow:

```bash
python -m agentbus.main --workflow multi --show-context "Create calculator tests"
```

Create a task branch and commit approved changes:

```bash
python -m agentbus.main --workflow multi --create-branch --commit "Add calculator tests"
```

Create a branch, commit, push, and open a GitHub PR:

```bash
python -m agentbus.main --workflow multi --create-branch --commit --open-pr "Add calculator tests"
```

Useful overrides:

```bash
python -m agentbus.main "Create hello.py and run it" --model qwen2.5-coder:7b --workspace workspace --max-steps 15
```

Provider and role overrides:

```bash
python -m agentbus.main --provider azure --model default-deployment --planner-model planner-deployment --coder-model coder-deployment --reviewer-model reviewer-deployment --model-timeout 120 --workflow multi "Create calculator.py with tests"
```

## Multi-Agent Workflow

The multi-agent workflow is intentionally small and local:

- Planner receives a compact repo context pack, then creates a structured goal, implementation steps, test strategy, and done criteria.
- Coder reuses the existing AgentBus loop and tools to execute the plan.
- Verifier runs a detected safe test command when one is available.
- Reviewer checks the original task, plan, git diff, and verifier output, then approves or requests fixes.

If the reviewer rejects the result, AgentBus allows one retry through the Coder and Verifier before producing the final summary.

## Durable Execution

Durable mode is an opt-in extension of the existing multi-agent workflow. Regular mode keeps the original in-process Planner -> Coder -> Verifier -> Reviewer behavior. Durable mode uses the same agents and safety-controlled tools, but converts planner steps into a persistent SQLite-backed graph. Sequential durable execution remains the default. Explicit parallel mode adds a bounded foreground scheduler, transactional worker leases, isolated Git worktrees, and deterministic commit integration.

The durable sequence is:

1. Build the repo context and obtain structured planner output.
2. Validate task IDs, dependencies, and cycle freedom.
3. Persist the run, graph, and all initial task records atomically.
4. Select dependency-ready and approval-safe tasks, one at a time by default or up to the explicit parallel worker bound.
5. Run the Coder and Verifier, then review only that task's specification, expected outputs, artifacts, and bounded task diff.
6. Persist the result, retry if policy allows, or in parallel mode create one scoped task commit and integrate it in deterministic graph order.
7. After every graph task succeeds, run final verification and one mandatory whole-run review.
8. Commit and optionally open a PR only after the final reviewer approves.

Planner steps may include an optional `dependencies` list. If no planner step supplies dependencies, AgentBus preserves compatibility by creating a sequential graph: `step-1 -> step-2 -> step-3`. Parallelism is therefore available only when the validated plan contains independent ready tasks. No daemon or background worker is started; the bounded scheduler runs in the foreground.

### Durable State

SQLite is the source of truth for recovery. The default database is `.agentbus/state.db`, relative to the AgentBus process directory and separate from the default `workspace` target. Configure another location with:

```bash
set AGENTBUS_STATE_DIR=C:\agentbus-state
set AGENTBUS_STATE_DB=state.db
```

`AGENTBUS_STATE_DB` may also be an absolute database path. When the target repository is the process directory, point `AGENTBUS_STATE_DIR` outside that repository if runtime files must not live there. `.agentbus/` is ignored by this repository.

The database stores runs, task specifications and statuses, attempts, artifact references, approvals, worktrees, worker leases, task commits, integration attempts, and compact events. Schema version 2 is reached through a transactional version-1 migration; existing rows are preserved and a failed migration rolls back. `StateStore.backup()` provides an explicit SQLite backup operation before migration. JSONL files in `runs/` remain useful audit output, but AgentBus does not reconstruct recovery state from them. Secret-shaped keys and values are redacted from both stores, strings are bounded, and full environment dumps are not recorded.

### Isolated Parallel Execution

At parallel run creation, AgentBus validates that the configured workspace is the exact Git top-level and captures its full base commit SHA. It creates an AgentBus-owned integration worktree and one worktree per active task under a canonical root outside the target repository. Coder, filesystem, command, Git, verifier, and task-review operations receive only the task worktree path. The source checkout, its current branch, staged files, and unrelated changes are never used as the task execution surface.

The scheduler starts at most `min(AGENTBUS_MAX_WORKERS, ready tasks)` local threads. Each task must first acquire a persisted SQLite lease through a short `BEGIN IMMEDIATE` transaction. A task has at most one active non-expired lease. Heartbeats renew ownership, expiry permits reclamation, and every reclamation increments a monotonically increasing fencing token. A stale worker cannot persist task success with an old token. Providers are constructed per worker; only the locked usage ledger is shared.

A successful worker creates exactly one path-scoped task commit containing commit-eligible run-attributed files. Generated and ignored artifacts remain excluded. Task commits are cherry-picked into the integration worktree by topological level and then stable task ID. Downstream tasks are based only on an integration commit that already contains every successful dependency. A conflict is recorded with bounded repository-relative file names, the AgentBus-owned cherry-pick is aborted, and the run halts without modifying the user's checkout or guessing a resolution.

After all task commits integrate, final verification and the mandatory whole-run reviewer run against the integration worktree. Only an accepted integration may create the requested user-facing branch ref, commit identifier, push, or PR. The branch ref points at the verified integration commit but is not checked out or merged into the user's branch.

### Resume And Inspect

Durable creation prints the run ID before task execution. State-only operations do not prompt for a new task and do not run a model:

```bash
python -m agentbus.main --list-runs
python -m agentbus.main --show-run <run-id>
python -m agentbus.main --show-scheduler <run-id>
python -m agentbus.main --list-worktrees <run-id>
python -m agentbus.main --list-workers <run-id>
python -m agentbus.main --recover-leases <run-id>
python -m agentbus.main --resume <run-id>
python -m agentbus.main --cancel-run <run-id> --reason "Work no longer needed"
```

On sequential resume, a task left `running` is reconciled from its latest persisted attempt. On parallel resume, AgentBus first expires stale leases, leaves tasks with valid leases untouched, validates persisted worktrees, recovers a task commit created before state persistence when its history is unambiguous, and resumes interrupted integration safely:

- A still-running attempt becomes `interrupted`.
- A succeeded attempt whose task status was not yet updated is promoted to a succeeded task.
- An interrupted or failed attempt is retried only when its category and remaining attempt budget allow it.
- A succeeded, rejected, failed, blocked, or cancelled task is never selected for execution again.

Each retry creates a new attempt row and preserves numbering across process recreation. Retry delay is persisted as deterministic metadata for future scheduling; the current CLI does not sleep or launch a background retry worker. Model output and transport failures, command failures, verifier failures, reviewer corrections, and interruptions may be retried. Policy violations and unsafe tool validation failures are not blindly retried. The planner default is two attempts per task, bounded again by the engine retry policy.

If a process stops after a tool has changed a worktree but before task success is persisted, AgentBus records the attempt as interrupted and may retry it. A single clean task commit directly above the persisted worktree base can be recovered without rerunning its coder. This checkpoint provides recoverable local execution and duplicate-execution prevention under persisted local lease rules; it does not provide distributed exactly-once execution or transactional rollback for arbitrary external side effects. Task executors should therefore remain restart-tolerant.

Failed and rejected runs do not automatically reset, clean, delete, or roll back workspace files. Their reports retain created and modified file paths and state that edits remain for inspection. A future cleanup workflow may recommend bounded manual actions, but AgentBus never executes destructive cleanup automatically.

Worktree cleanup is also explicit. The following command considers only persisted AgentBus-owned worktrees for the named run, validates repository ownership, marks each cleanup request, and removes only clean worktrees. Dirty, missing, mismatched, or unknown paths are refused and reported. No force option is used.

```bash
python -m agentbus.main --cleanup-worktrees <run-id>
```

### Approval Gates

Low-risk tasks run normally. Medium-risk tasks are recorded in ready events but do not block by default. A high-risk task transitions to `waiting_for_approval` before the coder or any tool is invoked. Only an explicit CLI action can persist a decision:

```bash
python -m agentbus.main --approve <run-id>:<task-id> --reason "Reviewed locally"
python -m agentbus.main --resume <run-id>
```

Reject unsafe work with:

```bash
python -m agentbus.main --reject <run-id>:<task-id> --reason "Unsafe migration"
```

Rejection marks the task rejected, propagates blocked state to its dependents, fails the run when no valid progress remains, and prevents Git commit or PR finalization. Model output cannot create an approval.

### Durable Git Safety

The existing Git flags work with durable mode:

```bash
python -m agentbus.main --workflow multi --durable --create-branch --commit "Add calculator tests"
python -m agentbus.main --workflow multi --durable --create-branch --commit --open-pr "Add calculator tests"
```

The configured durable workspace is canonicalized to an absolute path and must equal `git rev-parse --show-toplevel` when that command is run inside the workspace. A nested directory that would make Git walk into a parent repository is rejected with `WorkspaceRepositoryMismatch`; AgentBus never collects diffs or changed files from that parent. Changed paths are target-repository-relative, diff input is bounded, and durable commits stage only files attributed to the run.

In sequential mode, branch creation may occur before planning when requested. In parallel mode, no user-facing branch is created until the integrated result passes final verification and review. Commit and PR options are persisted with the run, and final rejection fails the run without rewriting successful task, attempt, task-commit, or integration history. PR creation remains explicitly opt-in. A clean Git HEAD that moved after a sequential commit-start event can be reconciled after a crash, preventing a second commit from being created during resume.

### Generated Artifact Hygiene

AgentBus applies one conservative repository-relative artifact policy to Python, Node, Java/build, editor, OS, and AgentBus runtime outputs. Reports distinguish the complete run-attributed audit inventory from relevant review files, generated artifacts, Git-ignored paths, review exclusions, and commit-eligible files. Normal untracked source and unknown files remain visible; they are never hidden merely because they are untracked.

Auto-detected pytest verification runs as `python -B -m pytest -p no:cacheprovider` with `PYTHONDONTWRITEBYTECODE=1` in a sanitized child environment. Ordinary process settings remain available, while common key, token, secret, password, credential, package-index, and authentication variables are removed. This prevents Python bytecode and pytest cache noise without mutating `os.environ` or forwarding provider keys to repository tests. Explicit verifier commands keep their original arguments; Python commands still receive the safe bytecode environment policy.

Known untracked or Git-ignored generated outputs are excluded from semantic review. Tracked generated-looking files remain visible to the reviewer because the repository has intentionally versioned them. Commit selection is stricter: only relevant run-attributed paths are passed to path-scoped Git commit operations, generated artifacts are never staged, and unrelated staged or pre-existing changes remain untouched.

Recommended repository exclusions include:

```gitignore
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
coverage.xml
htmlcov/
node_modules/
coverage/
.next/
dist/
target/
build/
.gradle/
.agentbus/
runs/
```

Artifact classification never deletes files. AgentBus prevents avoidable creation, filters safe generated noise from review and commits, and reports paths for inspection. Cleanup remains an explicit manual or future opt-in operation because a matching path may contain user-owned data. This is not filesystem rollback or sandbox isolation.

## Repo Context Builder

Before multi-agent planning, AgentBus scans the configured workspace and builds a compact text summary for the agents. The context pack includes:

- Workspace overview, files, directories, and ignored generated folders.
- Detected languages, frameworks, and package managers.
- Important files, config files, entrypoints, and test files.
- A suggested test command and confidence.
- Obvious task-relevant file names when the task mentions them.
- Safety notes for workspace-only file access and `shell=False` commands.

Detection is heuristic-based and dependency-free. Examples:

- `requirements.txt`, `pyproject.toml`, `pytest.ini`, or Python tests suggest Python and `python -m pytest`.
- `package.json` with a `test` script suggests Node.js and `npm test`.
- `pom.xml` suggests Maven Java and `mvn test`.
- `build.gradle` or `build.gradle.kts` suggests Gradle and `gradle test`.
- `go.mod` suggests Go and `go test ./...`.
- `Cargo.toml` suggests Rust and `cargo test`.

## Git And PR Workflow

AgentBus can optionally manage a local engineering workflow after the multi-agent reviewer approves the work:

- `--create-branch` creates a safe task branch before coding.
- `--branch-name` supplies an explicit branch name.
- `--commit` commits approved changes after verifier success and reviewer approval.
- `--open-pr` pushes the branch and opens a GitHub PR with the `gh` CLI.
- `--pr-base` selects the PR base branch, defaulting to `main`.

PR creation is opt-in only. AgentBus does not open a PR unless `--open-pr` is passed explicitly, and it requires a successful commit first. The generated PR body includes the user task, planner summary, verifier result, reviewer summary, changed files, test command, and safety notes.

The runner also reads these environment variables:

- `AGENTBUS_MODEL`
- `AGENTBUS_OLLAMA_URL`
- `AGENTBUS_WORKSPACE`
- `AGENTBUS_RUNS_DIR`
- `AGENTBUS_STATE_DIR`
- `AGENTBUS_STATE_DB`
- `AGENTBUS_MAX_STEPS`
- `AGENTBUS_COMMAND_TIMEOUT`
- `AGENTBUS_MAX_HISTORY_CHARS`
- `AGENTBUS_PARALLEL_EXECUTION` (default `false`)
- `AGENTBUS_MAX_WORKERS` (default `1`)
- `AGENTBUS_WORKER_LEASE_SECONDS` (default `120`)
- `AGENTBUS_WORKER_HEARTBEAT_SECONDS` (default `30`, less than half the lease)
- `AGENTBUS_WORKTREE_ROOT` (default is a workspace-specific sibling path)
- `AGENTBUS_KEEP_WORKTREES` (default `true`)
- `AGENTBUS_INTEGRATION_STRATEGY` (currently `cherry-pick` only)
- `AGENTBUS_PROVIDER`
- `AGENTBUS_FALLBACK_PROVIDER`
- `AGENTBUS_ENABLE_PROVIDER_FALLBACK`
- `AGENTBUS_MODEL_TIMEOUT_SECONDS`
- `AGENTBUS_MODEL_MAX_RETRIES`
- `AGENTBUS_MODEL_RETRY_BASE_SECONDS`
- `AGENTBUS_MODEL_RETRY_MAX_SECONDS`
- `AGENTBUS_PLANNER_MODEL`
- `AGENTBUS_CODER_MODEL`
- `AGENTBUS_REVIEWER_MODEL`
- `AGENTBUS_SUMMARIZER_MODEL`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_AUTH_MODE`
- `AZURE_OPENAI_API_MODE`
- `AZURE_OPENAI_DEFAULT_DEPLOYMENT`
- `AZURE_OPENAI_PLANNER_DEPLOYMENT`
- `AZURE_OPENAI_CODER_DEPLOYMENT`
- `AZURE_OPENAI_REVIEWER_DEPLOYMENT`
- `AZURE_OPENAI_SUMMARIZER_DEPLOYMENT`
- `AZURE_OPENAI_TIMEOUT_SECONDS`
- `AZURE_OPENAI_MAX_RETRIES`

## Safety Model

AgentBus is intentionally local and conservative:

- File reads and writes are restricted to the configured workspace directory.
- Path traversal and absolute file paths are blocked.
- Commands must be JSON arrays of strings and run with `shell=False`.
- Only a small command allowlist is available.
- Obvious destructive commands, destructive arguments, shell syntax, and remote access helpers are blocked.
- Verifier subprocesses remove common credential-bearing environment variables.
- Git automation uses safe `git` subprocess calls with `shell=False`.
- AgentBus does not reset, clean, rebase, force push, deploy, or apply infrastructure.
- Run logs are JSONL files in the configured runs directory and do not include environment variables or secrets.
- API keys are held only in provider configuration/client state and are excluded from config repr, diagnostics, logs, durable metadata, and exceptions.
- Full model prompts, model responses, file contents, and command output are not written to normal provider audit events.

## Current Limitations

- AgentBus is a local runner, not a multi-tenant service.
- The multi-agent workflow supports one reviewer retry for now.
- Parallel execution is bounded, local, foreground, and opt-in. There is no daemon, remote queue, multi-host consensus, distributed lease, or speculative execution.
- Durable recovery cannot roll back arbitrary filesystem, command, network, or other external side effects. Interrupted tasks may run again within their bounded attempt policy unless a safe task commit is recovered.
- Parallel mode requires an isolated target Git repository root. Non-Git workspaces and nested directories that resolve to a parent repository are refused.
- Integration conflicts halt for explicit human action; AgentBus does not automatically resolve, reset, clean, merge into, or check out the user's branch.
- Worktrees and internal refs are retained for diagnostics by default. Cleanup is explicit and refuses dirty or unowned paths.
- SQLite schema version 2 has an explicit migration from version 1. Future schema changes still require registered transactional migrations.
- Repo context is heuristic-based; it does not read whole file contents, build embeddings, or infer complex architecture.
- There is no web dashboard, authentication, billing, cloud deployment, or Kubernetes integration.
- There is no complex vector memory yet.
- PR creation depends on the GitHub CLI being installed and authenticated.
- Model quality, capability, quota, and latency depend on the selected local model or Azure deployment.
- Azure authentication supports API keys in this checkpoint. Entra ID and managed identity are a future enhancement.
- AgentBus does not discover or provision Azure resources/deployments and does not automatically switch API modes.
- Some Azure deployments do not support Responses API or strict structured outputs; select a compatible deployment or configure `chat_completions` explicitly.
- The command allowlist is intentionally narrow, so some legitimate workflows may need explicit tool support before they are available.

## Project Information

- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Support](SUPPORT.md)
- [Release checklist](RELEASE_CHECKLIST.md)
- [Release process](docs/release-process.md)
- [MIT license](LICENSE)
