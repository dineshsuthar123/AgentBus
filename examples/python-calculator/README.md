# Python calculator example

Copy this directory outside the AgentBus source checkout, then initialize it as
its own repository. Running it in place would correctly fail AgentBus's exact
Git-root check because the directory belongs to the parent repository.

```powershell
$example = Join-Path $env:TEMP "agentbus-calculator"
Copy-Item -Recurse examples\python-calculator $example
Set-Location $example
git init
git config user.name "AgentBus Example"
git config user.email "agentbus@example.invalid"
git add .
git commit -m "example baseline"
agentbus init --local --provider ollama
```

POSIX uses the same flow with `cp -R`, `cd`, and `.agentbus/config.toml` paths.

## Workflows

Ollama single workflow:

```powershell
agentbus run --config .agentbus\config.toml --workflow single "Implement TASK.md"
```

Azure durable workflow after setting environment credentials:

```powershell
agentbus run --config .agentbus\config.toml --provider azure --workflow multi --durable "Implement TASK.md"
```

Durable parallel workflow:

```powershell
agentbus run --config .agentbus\config.toml --workflow multi --durable --parallel --max-workers 2 "Implement code, tests, and documentation from TASK.md as independent tasks where possible"
```

Inspect the printed run ID without invoking a model:

```powershell
agentbus show-run RUN_ID --config .agentbus\config.toml
```

Run the network-free AgentBus acceptance suite:

```powershell
agentbus evaluate run --suite release-offline --variant durable-parallel-fake
```

Expected flow: Planner creates bounded tasks, Coder edits only this repository,
Verifier runs pytest, final Reviewer evaluates the complete result, and AgentBus
reports changed files. A failed run leaves edits for inspection and does not
reset or delete them.
