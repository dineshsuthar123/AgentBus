# Five-minute quickstart

The deterministic provider is built in, network-free, and exercises the same
structured planning, managed tools, verification, final review, and reporting
paths used by configured model providers.

## 1. Prove the installation

Run the disposable quickstart first:

```console
agentbus quickstart --json
```

AgentBus creates a temporary demo repository, indexes it, writes and tests a
small calculator through managed tools, verifies and reviews the result, and
removes its owned temporary files. Add `--keep-demo` only when you want to
inspect that disposable repository.

## 2. Configure a disposable repository

Run the first real task in a clean Git repository, not in a repository that
contains unrelated uncommitted work.

```console
git init agentbus-demo
cd agentbus-demo
agentbus setup --workspace . --provider deterministic --scope workspace --non-interactive --dry-run
agentbus setup --workspace . --provider deterministic --scope workspace --non-interactive
agentbus doctor --workspace . --provider deterministic --json
```

The dry run reports paths without writing. Workspace setup creates
`.agentbus/config.toml` and local runtime state; it does not write credentials
or contact a provider.

## 3. Build the index and run a task

```console
agentbus index build --workspace . --json
agentbus run --workspace . --provider deterministic --workflow multi --durable "Create and verify a small calculator"
agentbus runs
```

The deterministic profile creates `agentbus_result.py` and
`test_agentbus_result.py`. A successful final review is mandatory before an
optional commit or pull request can be created.

## 4. Inspect and replay

Replace `<run-id>` with the identifier printed by the run:

```console
agentbus show-run <run-id>
agentbus replay <run-id> --mode offline --json
agentbus trace verify <run-id> --json
```

Offline replay uses captured, sanitized envelopes and reports zero provider and
network calls. It does not silently fall back to Azure or Ollama.

## 5. Clean only owned stale state

```console
agentbus cleanup --dry-run --stale --json
```

AgentBus never automatically resets a repository or rolls back files after a
failed run. Inspect `show-run`, the reported changed files, and `git diff`
before deciding what to keep or remove.

Next, choose a [provider](../guides/providers.md) or open the
[VS Code guide](vscode.md).
