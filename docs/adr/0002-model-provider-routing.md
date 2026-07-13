# ADR 0002: Model Provider Routing

- Status: Accepted
- Date: 2026-07-10

## Context

AgentBus began as a local Ollama runtime. Planner, coder, and reviewer behavior should not depend on one network SDK, while users need an Azure OpenAI option with role-specific deployments, structured output, bounded retries, safe diagnostics, and durable metadata. Cloud support must not weaken local/offline operation, tool controls, approvals, verification, review, or durable execution.

## Decision

AgentBus uses a provider-neutral `ModelProvider` contract and normalized `ModelResult`/error types. Agents bind to role-specific `RoutedModel` facades. A central router resolves provider, model/deployment, role, timeout, retry budget, and explicit fallback. Provider adapters contain SDK and transport details.

Ollama remains supported and is the default. Existing dictionary/string call sites use a compatibility facade while normalized results remain available for logging, usage aggregation, and durable attempt metadata.

Azure uses the official OpenAI Python SDK with an Azure OpenAI v1 base URL. Responses API is primary; chat completions is an explicit mode. Deployment names are kept distinct from public model identifiers. Client construction and SDK import are lazy, SDK retries are disabled, and the adapter owns exception normalization.

Planner, coder/action, and reviewer calls provide Pydantic schemas. Providers request schema-constrained output where supported, and AgentBus always validates locally. Raw dictionary schemas receive local JSON Schema validation. There is no implicit downgrade from structured output to unconstrained text.

Role resolution supports `default`, `planner`, `coder`, `reviewer`, and `summarizer`. Azure chooses a role deployment and then the Azure default deployment. Safe provider/deployment identifiers and usage are persisted; SDK clients and response objects are not.

Provider retries are an inner, bounded model-call mechanism for normalized transient failures. Durable retries remain an outer task-attempt mechanism. This separation ensures a provider retry does not create a durable attempt and prevents unbounded retry multiplication.

Automatic fallback is disabled by default. The only supported path is Azure to Ollama after retryable Azure failures exhaust provider retries. Authentication, authorization, configuration, deployment, bad-request, content-policy, output/schema, safety, and approval failures never trigger fallback. Fallback events and results retain original failure provenance.

API-key authentication is implemented now. Entra ID and managed identity remain a future adapter enhancement rather than a partial or fake mode.

## Consequences

Positive consequences:

- Agents, orchestration, and durable state remain provider-neutral.
- Ollama continues to work with no Azure credentials or SDK client construction.
- Azure behavior and exception mapping are centralized and offline-testable.
- Role-specific deployments can evolve independently.
- Retry, fallback, usage, and diagnostics are observable and bounded.
- Fallback cannot bypass existing approval, verification, review, commit, or PR gates.

Tradeoffs:

- Configuration has more provider and role fields.
- Provider retries plus durable retries can repeat calls, although both budgets are finite.
- Capability differences remain deployment-specific; AgentBus cannot guarantee every Azure deployment supports Responses or strict structured outputs.
- Usage is persisted as event/attempt JSON rather than a query-optimized database table.

## Alternatives Considered

Direct Azure conditionals in agents were rejected because they couple workflow behavior to SDK details and duplicate routing/error logic.

Replacing Ollama with Azure was rejected because AgentBus is local-first and requires an offline/default provider.

SDK-managed retries were rejected because combined hidden and durable retries would be difficult to bound or observe.

Fallback on every provider error was rejected because auth, configuration, policy, and schema errors must remain visible and fail closed.

Persisting Azure response objects or IDs as workflow truth was rejected because SQLite state transitions, not remote conversation state, define durable execution.

## Future Work

- Add Entra ID/managed identity with the official token-provider pattern and lazy optional `azure-identity` dependency.
- Add providers through the same contract and normalized error model.
- Add capability discovery or explicit deployment capability declarations without silent API-mode switching.
- Add richer usage export/reporting without hardcoded pricing.
