# Change Impact Analysis

Change-impact analysis accepts contained repository-relative paths or persisted
symbol identities. It resolves the subjects against one index snapshot and
performs bounded typed graph traversal. Results can include changed symbols,
direct and transitive dependents, affected projects, public APIs, endpoints,
configuration units, ownership rules, architecture crossings, integration
hotspots, and relevant tests.

## Evidence and risk

Every result carries a snapshot ID, deterministic result ID, confidence,
evidence, uncertainty, truncation state, and a qualitative risk level. Risk is
based on the kinds of affected evidence, such as public API or endpoint reach,
architecture crossings, ownership boundaries, configuration, unresolved edges,
and stale state. File count alone is not treated as semantic risk.

Confidence is not a probability. It summarizes static evidence quality. Common
uncertainty reasons include:

- stale or partially current index state;
- an unknown or protected subject;
- unresolved imports, calls, or references;
- dynamic language behavior;
- graph depth, node, edge, or result truncation;
- a proposed dependency not present in the captured graph;
- incomplete project or test metadata.

Protected subjects and protected proposed dependencies are omitted and reported
as uncertainty. Their identities or content are not copied into the result.

## Test selection

`agentbus tests-for` uses typed `tests` edges, project test roots, naming
conventions, ownership, architecture crossings, and impact evidence. It returns:

- mandatory tests that available evidence directly requires;
- optional tests with weaker or transitive evidence;
- escalation reasons;
- a `full_suite_recommended` decision when bounded selection is not safe enough.

Mandatory tests survive output truncation. If test evidence is missing, stale,
protected, or too broad, selection fails toward a wider test recommendation
rather than claiming a precise minimal set. AgentBus does not execute selected
tests during analysis; the verifier remains responsible for real execution.

## Examples

```powershell
agentbus impact services/python_service/calculator.py --evidence
agentbus impact SYMBOL_ID --depth 4 --max-nodes 500 --json
agentbus tests-for services/python_service/calculator.py --evidence
```

Use explicit project and language filters only when the task is intentionally
scoped. A filter can exclude real downstream effects, so filtered results retain
that scope as evidence.

## Limitations

The graph is conservative static evidence. Reflection, runtime registration,
dependency injection, generated code, build tags, macros, framework magic,
network contracts, database schemas, and external consumers may not be visible.
Passing impact analysis and selected tests does not prove a change safe. Final
verification and review remain mandatory.

See [Hybrid Retrieval](hybrid-retrieval.md),
[Context Planning](context-planning.md), and
[Repository Intelligence](repository-intelligence.md).
