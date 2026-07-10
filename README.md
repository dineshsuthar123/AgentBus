# AgentBus

AgentBus Local Runner is a local coding runtime for small software engineering tasks. It asks an Ollama-hosted coding model for JSON actions, executes a small set of workspace-scoped tools, records machine-readable run logs, and stops when the work is finished or the configured step limit is reached.

## Setup

Create and activate a virtual environment, then install the project dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Install Ollama and pull the default local model:

```bash
ollama pull qwen2.5-coder:7b
```

## Run Tests

```bash
python -m pytest
```

## Run The CLI

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

## Multi-Agent Workflow

The multi-agent workflow is intentionally small and local:

- Planner receives a compact repo context pack, then creates a structured goal, implementation steps, test strategy, and done criteria.
- Coder reuses the existing AgentBus loop and tools to execute the plan.
- Verifier runs a detected safe test command when one is available.
- Reviewer checks the original task, plan, git diff, and verifier output, then approves or requests fixes.

If the reviewer rejects the result, AgentBus allows one retry through the Coder and Verifier before producing the final summary.

## Durable Execution

Durable mode is an opt-in extension of the existing multi-agent workflow. Regular mode keeps the original in-process Planner -> Coder -> Verifier -> Reviewer behavior. Durable mode uses the same agents and safety-controlled tools, but converts planner steps into a persistent graph and executes one task at a time through a SQLite-backed state machine.

The durable sequence is:

1. Build the repo context and obtain structured planner output.
2. Validate task IDs, dependencies, and cycle freedom.
3. Persist the run, graph, and all initial task records atomically.
4. Mark one dependency-ready task as running and create its attempt before invoking the coder.
5. Run the existing Coder, Verifier, and Reviewer for that graph task.
6. Persist the result, retry if policy allows, or block dependent tasks.
7. Commit and optionally open a PR only after every graph task succeeds.

Planner steps may include an optional `dependencies` list. If no planner step supplies dependencies, AgentBus preserves compatibility by creating a sequential graph: `step-1 -> step-2 -> step-3`. Execution is deterministic and sequential in this checkpoint; no background worker or parallel task execution is started.

### Durable State

SQLite is the source of truth for recovery. The default database is `.agentbus/state.db`, relative to the AgentBus process directory and separate from the default `workspace` target. Configure another location with:

```bash
set AGENTBUS_STATE_DIR=C:\agentbus-state
set AGENTBUS_STATE_DB=state.db
```

`AGENTBUS_STATE_DB` may also be an absolute database path. When the target repository is the process directory, point `AGENTBUS_STATE_DIR` outside that repository if runtime files must not live there. `.agentbus/` is ignored by this repository.

The database stores runs, task specifications and statuses, attempts, artifact references, approvals, and compact events. JSONL files in `runs/` remain useful audit output, but AgentBus does not reconstruct recovery state from them. Secret-shaped keys and values are redacted from both stores, strings are bounded, and full environment dumps are not recorded.

### Resume And Inspect

Durable creation prints the run ID before task execution. State-only operations do not prompt for a new task and do not run a model:

```bash
python -m agentbus.main --list-runs
python -m agentbus.main --show-run <run-id>
python -m agentbus.main --resume <run-id>
python -m agentbus.main --cancel-run <run-id> --reason "Work no longer needed"
```

On resume, a task left `running` is reconciled from its latest persisted attempt:

- A still-running attempt becomes `interrupted`.
- A succeeded attempt whose task status was not yet updated is promoted to a succeeded task.
- An interrupted or failed attempt is retried only when its category and remaining attempt budget allow it.
- A succeeded, rejected, failed, blocked, or cancelled task is never selected for execution again.

Each retry creates a new attempt row and preserves numbering across process recreation. Retry delay is persisted as deterministic metadata for future scheduling; the current CLI does not sleep or launch a background retry worker. Model output and transport failures, command failures, verifier failures, reviewer corrections, and interruptions may be retried. Policy violations and unsafe tool validation failures are not blindly retried. The planner default is two attempts per task, bounded again by the engine retry policy.

If a process stops after a tool has changed the workspace but before task success is persisted, AgentBus records the attempt as interrupted and may retry it. This checkpoint does not provide filesystem rollback or exactly-once semantics for arbitrary external side effects. Task executors should therefore be restart-tolerant.

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

Branch creation may occur before planning when requested. Commit and PR options are persisted with the run, but finalization occurs only after the durable status is `succeeded` and the latest verifier and reviewer statuses are both successful. PR creation remains explicitly opt-in and still requires a successful commit. A clean Git HEAD that moved after a commit-start event can be reconciled after a crash, preventing a second commit from being created during resume.

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

## Safety Model

AgentBus is intentionally local and conservative:

- File reads and writes are restricted to the configured workspace directory.
- Path traversal and absolute file paths are blocked.
- Commands must be JSON arrays of strings and run with `shell=False`.
- Only a small command allowlist is available.
- Obvious destructive commands, destructive arguments, shell syntax, and remote access helpers are blocked.
- Git automation uses safe `git` subprocess calls with `shell=False`.
- AgentBus does not reset, clean, rebase, force push, deploy, or apply infrastructure.
- Run logs are JSONL files in the configured runs directory and do not include environment variables or secrets.

## Current Limitations

- AgentBus is a local runner, not a multi-tenant service.
- The multi-agent workflow supports one reviewer retry for now.
- Durable graph execution is sequential and runs in the foreground; there is no scheduler, daemon, distributed lease, or parallel execution.
- Durable recovery cannot roll back partially completed filesystem or command side effects. Interrupted tasks may run again within their bounded attempt policy.
- Tasks share one workspace. Per-task Git worktrees and isolated merge coordination are not implemented yet.
- SQLite schema version 1 is guarded explicitly; future schema changes require registered migrations.
- Repo context is heuristic-based; it does not read whole file contents, build embeddings, or infer complex architecture.
- There is no web dashboard, authentication, billing, cloud deployment, or Kubernetes integration.
- There is no complex vector memory yet.
- PR creation depends on the GitHub CLI being installed and authenticated.
- Model quality and latency depend on the local Ollama model and machine.
- The command allowlist is intentionally narrow, so some legitimate workflows may need explicit tool support before they are available.
