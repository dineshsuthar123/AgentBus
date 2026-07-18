# Control Protocol v1

The authoritative artifacts are:

- `protocol/agentbus-v1.openapi.json`
- `protocol/agentbus-v1.schema.json`
- `extensions/vscode/src/generated/protocol.ts`

They are generated from the FastAPI application and Pydantic models. Run
`agentbus control-schema export` after model changes and
`agentbus control-schema export --check` in verification.

All errors use `{ "error": { "code", "message", "retryable", "details" } }`.
SSE event IDs are persisted monotonic SQLite IDs. Clients discard duplicate or
stale sequences and reconnect with `Last-Event-ID`.

Cancellation lifecycle fields are backward-compatible protocol v1 extensions:
all nested objects have defaults, and older payloads without `cancellation`
remain valid. `RunSummary`, `SchedulerResponse`, `RunReportResponse`, and
`CancelResponse` expose the same typed lifecycle. The report body additionally
retains bounded execution diagnostics for CLI and support inspection.

Cancellation events are:

- `cancellation_requested`
- `cancellation_propagated`
- `provider_cancellation_requested`
- `provider_cancellation_acknowledged`
- `operation_completed_after_cancellation`
- `scheduling_stopped`
- `run_cancelled`
- `cancellation_cleanup_completed`

Payloads contain lifecycle facts, repository-relative identifiers, and bounded
labels only. They do not contain prompts, bearer tokens, API keys, environment
dumps, or raw provider objects.
