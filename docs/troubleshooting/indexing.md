# Repository-index troubleshooting

```console
agentbus index status --workspace . --json
agentbus index verify --workspace . --json
agentbus doctor --workspace . --verbose --json
```

An absent index is not fatal; AgentBus falls back to the bounded repository
scanner. Stale or partial evidence is labeled and must not be treated as
authorization.

Use an incremental update after normal file changes:

```console
agentbus index update --workspace . --json
```

Use `index repair` only after `verify` reports a repairable issue. Review
`index clear --help` before clearing; it is an explicit destructive operation
against the selected local index, not the repository.

If a workspace is nested inside an unrelated parent Git repository, AgentBus
rejects it unless the selected workspace is its own exact repository root.
Open the actual repository root or initialize an isolated repository rather
than bypassing the boundary.

See [Repository intelligence](../guides/repository-intelligence.md) and
[Incremental indexing](../incremental-indexing.md).
