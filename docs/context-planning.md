# Context Planning

The context planner converts repository-intelligence candidates into a stable,
role-specific plan under explicit byte and token budgets. It augments the
existing context-pack builder; it does not bypass workspace containment,
protected-file rules, task scope, or the existing scanner fallback.

## Inputs

A plan is bound to:

- one repository and workspace identity;
- one index snapshot and state;
- a hash of the task text, not persisted prompt text;
- an agent role: planner, coder, verifier, or reviewer;
- optional changed paths and project filters;
- byte, token, candidate, graph, and evidence limits.

Task text is used locally for retrieval. Public plan summaries expose hashes,
paths, symbol metadata, reasons, confidence, uncertainty, and budget counts.
They do not expose raw source, prompt bodies, personal absolute paths, API keys,
or protected-file names.

## Role-specific selection

- Planner plans emphasize project structure, task-relevant symbols,
  architecture boundaries, ownership, and likely implementation/test areas.
- Coder plans emphasize exact implementation files, definitions, signatures,
  dependencies, and nearby tests.
- Verifier plans emphasize affected tests, test roots, public behavior,
  configuration, and uncertainty that should widen verification.
- Reviewer plans emphasize the task diff, dependents, public APIs, architecture
  crossings, ownership, selected tests, and unresolved risk.

Role weighting changes candidate order, not repository scope. A role cannot use
the index to claim files outside the current task or workspace.

## Determinism and budgets

Candidate identities and component scores are sorted deterministically. The
planner admits candidates while both byte and token estimates remain within the
request budget. It records selected counts and stable plan/hash identities.
Equivalent inputs and snapshot state produce the same plan regardless of parser
worker completion order.

Budget estimates are conservative planning values, not provider billing counts.
If a high-value candidate cannot fit, the plan reports omission/truncation rather
than exceeding limits. Large files are represented by bounded metadata and
existing safe context extraction, never by an unbounded full-file read.

## Stale and unavailable indexes

A stale or partial snapshot adds a warning and uncertainty to the plan. Runtime
scope validation checks that persisted context claims still belong to the exact
run workspace. If no valid index source is available, AgentBus continues with
the existing repository scanner and bounded context pack. It never silently
loads an index for a parent or different workspace.

Trace evidence records snapshot, graph, retrieval, context-plan, and context
hash identities. Providerless replay reuses captured sanitized evidence and can
compare it with the current local index. Drift is reported as index snapshot,
graph, retrieval-result, or context-plan drift; it does not silently rewrite the
original trace.

## Usage

```powershell
agentbus context-plan "Change calculator rounding" --role planner --evidence
agentbus context-plan "Review calculator API compatibility" --role reviewer --json
```

The VS Code `Context Plan` view exposes the same bounded candidates and budget
accounting. See [VS Code Extension](vscode-extension.md) and
[Deterministic Replay](deterministic-replay.md).
