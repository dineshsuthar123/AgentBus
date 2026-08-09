# Repository intelligence

The optional local index parses Python, TypeScript/JavaScript, Java, and Go
without importing repository modules, running builds, invoking package
managers, or uploading source. The normal scanner remains available if the
index is absent.

```console
agentbus index build --workspace . --json
agentbus index status --workspace . --json
agentbus search calculate_total --workspace . --evidence
agentbus impact src/calculator.py --workspace .
agentbus tests-for src/calculator.py --workspace .
agentbus context-plan "Change calculator rounding" --role reviewer --workspace .
```

Index results are advisory evidence, not authorization. Filesystem containment,
tool policy, and Git repository validation are performed independently.

Use `index update` for incremental changes and `index verify` to check stored
integrity. `index repair` and `index clear` are explicit operations; review
their help and target paths first.

See [Repository Intelligence](../repository-intelligence.md),
[Incremental Indexing](../incremental-indexing.md),
[Change Impact Analysis](../change-impact-analysis.md), and
[Context Planning](../context-planning.md).
