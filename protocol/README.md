# AgentBus Control Protocol v1

This directory is generated from the Python control-plane application and
Pydantic transport models.

- `agentbus-v1.openapi.json` describes the authenticated HTTP and SSE API.
- `agentbus-v1.schema.json` contains the shared transport model definitions.
- `../extensions/vscode/src/generated/protocol.ts` is generated from the JSON Schema.

Run `agentbus control-schema export` after changing protocol models. Run
`agentbus control-schema export --check` in CI to detect stale artifacts.

The health endpoint is unauthenticated. Every `/api/v1` endpoint uses an opaque
bearer token delivered once through the daemon parent-process handshake.
Generated files contain no concrete token, credential, or machine-specific path.
