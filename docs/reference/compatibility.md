# Compatibility and product identity

Syndra follows semantic versioning as a project and PEP 440 spelling for the
Python package. Before 1.0, the minor version is the compatibility line:
`0.6.x` components are intended to work together; a later minor release may
contain documented breaking changes.

## Canonical identity

New installations and documentation use:

- the `syndra` distribution, import package, and CLI;
- `SYNDRA_*` environment variables;
- `[syndra]` configuration tables;
- `.syndra` workspace state and configuration paths;
- `syndra-vscode` and Syndra Studio.

Run `syndra version --json` and `syndra upgrade-check --json` before an
upgrade. New configuration always takes precedence over a corresponding
legacy value. If both forms are set differently, Syndra emits a deterministic
conflict warning rather than silently selecting by process order.

## Legacy AgentBus adapters

The `agentbus` executable and Python import remain compatibility aliases for
the same implementation. Existing `AGENTBUS_*` variables, `[agentbus]`
configuration tables, and `.agentbus` state remain readable. When a workspace
has `.syndra`, it is authoritative; otherwise Syndra discovers existing
`.agentbus` state without moving, deleting, or rewriting it.

The VS Code extension retains established `agentbus.*` command, view, setting,
and URI-scheme IDs so existing keybindings and workspace settings continue to
work. Their visible labels and the extension package identity are Syndra.

## Immutable historical contracts

Branding does not rewrite authenticated or persisted evidence. Existing
SQLite databases, run IDs, evidence hashes, `agentbus_version` wire fields,
`agentbus-v1` protocol artifact names, `.agentbus-trace` archives, and
`application/vnd.agentbus.*` media types remain stable. Historical runs and
trace archives continue to validate and replay under the Syndra CLI.

## Versioned surfaces

- CLI behavior and safe defaults remain stable within a minor line.
- Unknown configuration and credential-shaped keys fail closed.
- Control, tool, repository-intelligence, trace, state, and index schemas keep
  explicit versions.
- Incompatible daemons or extensions are rejected before execution.
- Supported migrations are explicit, backed up, and verified; downgrade and
  destructive state relocation are never automatic.
- Trace exports are validated as untrusted archives.

See [the changelog](../../CHANGELOG.md) for milestone-specific changes.
