# VS Code Extension

Install AgentBus with `pip install "agentbus[ide]"`, then package the extension
from `extensions/vscode` with `npm ci && npm run package`.

The extension discovers compatible daemons from the metadata-only registry,
retrieves known tokens from SecretStorage, or launches `agentbus serve --port 0
--json-ready`. It validates daemon identity and protocol version before use.

Native views show runs, task state, approvals, worktrees, and provider
readiness. Commands submit single, multi-agent, durable, or parallel tasks;
resume and cancel runs; decide approvals; open reports; and display native
before/after diffs.

The deterministic provider is available in the provider setting for offline
development and acceptance. It uses the normal control API, router, structured
output validation, durable scheduler, tools, verifier, and reviewer.

Cancellation is stateful rather than a single spinner. The run tree, report,
and output channel distinguish:

- `Cancelling...`
- provider cancellation signalled;
- cancellation acknowledged;
- waiting for an active non-interruptible provider operation;
- an operation completed after the request;
- scheduling stopped;
- cancelled;
- resume available or unavailable.

The cancel command suppresses duplicate requests while one is in flight and is
disabled after request acknowledgement or terminal state. Native reports retain
safe lifecycle details. Restart stops the SSE stream before the daemon, starts
a new authenticated loopback daemon, reconnects, and reloads persisted runs.

`npm run test:integration` launches VS Code Electron against a temporary Git
repository and the real local daemon. It completes one deterministic durable
parallel run, observes SSE/task transitions, opens the commit-backed native
diff and report, cancels a second active provider run, and verifies daemon
restart recovery. Provider credentials are removed from the test child
environment.

Execution and Git actions require VS Code Workspace Trust. Multi-root workspaces
always require an explicit repository selection. Read-only doctor diagnostics
remain available in untrusted workspaces.
