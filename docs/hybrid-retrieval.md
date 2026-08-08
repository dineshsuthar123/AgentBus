# Hybrid Retrieval

AgentBus combines deterministic lexical, symbol, dependency, project,
architecture, test, recent-change, and optional local semantic evidence. Every
result reports score components, rank, source hash, index state, and a bounded
explanation. Equal scores use stable identities as tie breakers.

## Lexical retrieval

The lexical index tokenizes bounded metadata fields rather than evaluating a
database query language. Default relative field importance favors identifiers,
qualified names, endpoints, paths, modules, signatures, projects, symbol kinds,
configuration keys, and documentation in that order. Exact identifiers and
phrases receive explicit bonuses. Filters can restrict project, language,
symbol kind, path prefix, or test files.

Queries are bounded in length and term count. Search results are paginated and
protected files are absent from the candidate set. Search does not persist or
return raw source bodies.

## Structural expansion

Hybrid retrieval starts with bounded lexical and optional semantic candidates,
then selects a small stable set of symbol anchors. Graph expansion may add
dependencies, dependents, tests, project neighbors, and architecture crossings
up to configured depth, node, and edge limits. Recent changed paths can receive
a small explicit boost.

The implementation defaults weight lexical scores by `0.65` and optional
semantic scores by `0.30`, then applies separate structural bonuses. These are
ranking defaults, not probabilities. Component scores remain visible so a
reviewer can explain why a candidate was selected.

Cycles, unresolved references, graph limits, and stale snapshots are surfaced
as uncertainty or truncation. The system does not infer a runtime call graph.

## Optional semantic retrieval

Semantic retrieval is disabled unless application code explicitly supplies a
local `SemanticEmbeddingProvider`. The provider descriptor must declare a stable
model fingerprint and that source is not sent off-device. AgentBus rejects a
provider that declares off-device source transfer.

The semantic layer:

- never downloads a model;
- never falls back to a hosted embedding API;
- excludes protected files and symbols;
- bounds chunks, characters, vectors, dimensions, and batches;
- persists only sanitized metadata and similarity cache records, not source
  text or embedding vectors;
- degrades to lexical and structural retrieval on provider failure.

There is currently no CLI or control-plane switch that silently enables a
semantic model. Integrators must opt in through the local Python interface.

## Interpreting results

High rank means the available static evidence matched the query and configured
signals. It does not prove relevance, correctness, ownership, or change safety.
Stale, partial, unresolved, or truncated evidence should lower confidence even
when the numerical score is high.

For task-level selection and budgets, see [Context Planning](context-planning.md).
For downstream dependencies and tests, see
[Change Impact Analysis](change-impact-analysis.md).
