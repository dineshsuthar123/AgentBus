# Getting started

AgentBus 0.1 is a local developer tool. Start in a disposable Git repository,
use least-privilege provider credentials, and inspect every change.

## 1. Install

From a source checkout:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\agentbus.exe --version
```

```bash
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/agentbus --version
```

Use `pip install -e '.[dev,azure]'` for development with Azure support. A core
install does not need Azure credentials, Ollama, or a network call at install
time. `pipx install .` is also supported from a source checkout.

## 2. Choose a provider

Ollama is the default. Install Ollama separately and explicitly obtain a model;
AgentBus never downloads one. Azure support requires the `azure` extra and
process environment values described in [configuration.md](configuration.md).

## 3. Initialize

Run this from the target repository:

```powershell
agentbus init --local --provider ollama
agentbus config validate --config .agentbus\config.toml
```

```bash
agentbus init --local --provider ollama
agentbus config validate --config .agentbus/config.toml
```

`init` creates no credentials and makes no provider calls. Preview with
`--dry-run`; request a placeholder-only environment template with
`--with-env-example`. Existing config is refused unless `--force` is explicit.

## 4. Diagnose offline

```powershell
agentbus doctor --config .agentbus\config.toml
agentbus doctor --config .agentbus\config.toml --json
```

Doctor is offline by default. Only `--live-provider ollama` or
`--live-provider azure` permits a provider request.

## 5. Run a task

```powershell
agentbus run --config .agentbus\config.toml --workflow multi --durable "Add a tested subtraction function"
```

The configured workspace must be the exact Git top-level. A nested directory
that resolves to a parent repository is rejected.

## 6. Inspect and resume

```powershell
agentbus runs --config .agentbus\config.toml
agentbus show-run RUN_ID --config .agentbus\config.toml
agentbus resume RUN_ID --config .agentbus\config.toml
```

Failed runs leave filesystem edits in place and report created or modified
files. AgentBus does not reset, clean, or roll back user data.

## 7. Evaluate offline

```powershell
agentbus-eval list
agentbus-eval run --suite release-offline --variant durable-parallel-fake
```

The release suite uses deterministic fake providers and no network. Real
repositories and live providers require separate explicit consent flags.

## 8. Understand the boundary

Read [SECURITY.md](../SECURITY.md), the README safety model, and
[benchmarking-real-repositories.md](benchmarking-real-repositories.md). Git
worktrees, command allowlists, review, and repository scoping reduce risk but
do not provide complete sandbox isolation or transactional rollback.
