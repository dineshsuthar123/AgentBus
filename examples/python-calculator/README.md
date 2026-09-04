# Python calculator example

Copy this directory outside the Syndra source checkout, then initialize it as
its own repository. Running it in place would correctly fail Syndra's exact
Git-root check because the directory belongs to the parent repository.

```powershell
$example = Join-Path $env:TEMP "syndra-calculator"
Copy-Item -Recurse examples\python-calculator $example
Set-Location $example
git init
git config user.name "Syndra Example"
git config user.email "syndra@example.invalid"
git add .
git commit -m "example baseline"
syndra init --local --provider ollama
```

POSIX uses the same flow with `cp -R`, `cd`, and `.syndra/config.toml` paths.

## Workflows

Ollama single workflow:

```powershell
syndra run --config .syndra\config.toml --workflow single "Implement TASK.md"
```

Azure durable workflow after setting environment credentials:

```powershell
syndra run --config .syndra\config.toml --provider azure --workflow multi --durable "Implement TASK.md"
```

Durable parallel workflow:

```powershell
syndra run --config .syndra\config.toml --workflow multi --durable --parallel --max-workers 2 "Implement code, tests, and documentation from TASK.md as independent tasks where possible"
```

Inspect the printed run ID without invoking a model:

```powershell
syndra show-run RUN_ID --config .syndra\config.toml
```

Run the network-free Syndra acceptance suite:

```powershell
syndra evaluate run --suite release-offline --variant durable-parallel-fake
```

Expected flow: Planner creates bounded tasks, Coder edits only this repository,
Verifier runs pytest, final Reviewer evaluates the complete result, and Syndra
reports changed files. A failed run leaves edits for inspection and does not
reset or delete them.
