# Five-minute quickstart

The deterministic provider is built in, network-free, and exercises the same
structured planning, managed tools, verification, final review, and reporting
paths used by configured model providers.

## 1. Prove the installation

Run the disposable quickstart first:

```console
syndra quickstart --json
```

Syndra creates a temporary demo repository, indexes it, writes and tests a
small calculator through managed tools, verifies and reviews the result, and
removes its owned temporary files. Add `--keep-demo` only when you want to
inspect that disposable repository.

## 2. Configure a disposable repository

Run the first real task in a clean Git repository, not in a repository that
contains unrelated uncommitted work.

```console
git init syndra-demo
cd syndra-demo
syndra setup --workspace . --provider deterministic --scope workspace --non-interactive --dry-run
syndra setup --workspace . --provider deterministic --scope workspace --non-interactive
syndra doctor --workspace . --provider deterministic --json
```

The dry run reports paths without writing. Workspace setup creates
`.syndra/config.toml` and local runtime state; it does not write credentials
or contact a provider.

## 3. Build the index and run a task

```console
syndra index build --workspace . --json
syndra run --workspace . --provider deterministic --workflow multi --durable "Create and verify a small calculator"
syndra runs
```

The deterministic profile creates `syndra_result.py` and
`test_syndra_result.py`. A successful final review is mandatory before an
optional commit or pull request can be created.

## 4. Inspect and replay

Replace `<run-id>` with the identifier printed by the run:

```console
syndra show-run <run-id>
syndra replay <run-id> --mode offline --json
syndra trace verify <run-id> --json
```

Offline replay uses captured, sanitized envelopes and reports zero provider and
network calls. It does not silently fall back to Azure or Ollama.

## 5. Clean only owned stale state

```console
syndra cleanup --dry-run --stale --json
```

Syndra never automatically resets a repository or rolls back files after a
failed run. Inspect `show-run`, the reported changed files, and `git diff`
before deciding what to keep or remove.

Next, choose a [provider](../guides/providers.md) or open the
[VS Code guide](vscode.md).
