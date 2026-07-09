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

Print the local repo context pack without running an agent:

```bash
python -m agentbus.main --workspace workspace --show-context
```

Print context first, then continue into the selected workflow:

```bash
python -m agentbus.main --workflow multi --show-context "Create calculator tests"
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

The runner also reads these environment variables:

- `AGENTBUS_MODEL`
- `AGENTBUS_OLLAMA_URL`
- `AGENTBUS_WORKSPACE`
- `AGENTBUS_RUNS_DIR`
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
- Run logs are JSONL files in the configured runs directory and do not include environment variables or secrets.

## Current Limitations

- AgentBus is a local runner, not a multi-tenant service.
- The multi-agent workflow supports one reviewer retry for now.
- Repo context is heuristic-based; it does not read whole file contents, build embeddings, or infer complex architecture.
- There is no web dashboard, authentication, billing, cloud deployment, or Kubernetes integration.
- There is no complex vector memory or GitHub PR automation yet.
- Model quality and latency depend on the local Ollama model and machine.
- The command allowlist is intentionally narrow, so some legitimate workflows may need explicit tool support before they are available.
