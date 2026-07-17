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
