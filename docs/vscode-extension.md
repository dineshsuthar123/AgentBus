# VS Code Extension

Install AgentBus with `pip install "agentbus[ide]"`, then package the extension
from `extensions/vscode` with `npm ci && npm run package`.

The extension discovers compatible daemons from the metadata-only registry,
retrieves known tokens from SecretStorage, or launches `agentbus serve --port 0
--json-ready`. It validates daemon identity and protocol version before use.

Native views show runs, task state, approvals, tool invocations, worktrees,
provider readiness, and configured MCP servers. Commands submit single,
multi-agent, durable, or parallel tasks; resume and cancel runs; decide
approvals; inspect and cancel tools; open reports and artifacts; inspect policy;
check MCP servers; and display native before/after diffs.

## Tool invocations and approvals

The Tool Invocations view shows the registered name, status, owning task,
policy outcome, approval state, budget usage, cancellation, truncation, and safe
error category. `AgentBus: Show Tool Invocation` opens a bounded Markdown
document with exact capabilities and scopes, resource limits and support,
artifacts, process metadata, and redacted output. It never renders raw HTML or
unescaped remote Markdown.

`AgentBus: Cancel Tool Invocation` requests cancellation for the owning run and
is available only while the invocation can still be cancelled. The UI does not
accept or signal an arbitrary PID.

Tool approvals appear beside task approvals but include a `Tool` label, exact
capability and path scope, executable, working directory, network destination,
policy rule, constraints, and budget. Approve and reject commands submit the
displayed persisted revision. Approval does not broaden scope; runtime
revalidation still occurs during resume.

`AgentBus: Open Tool Artifact` accepts only a repository-relative artifact that
the authenticated API returned for that invocation. Repository containment,
protected-path, binary, size, digest, and exact invocation checks remain active.

`AgentBus: Show Tool Policy` presents the bounded default policy. Policy
inspection is read-only and does not execute a diagnostic call unless the user
explicitly invokes the corresponding control operation.

## MCP servers

The MCP Servers view lists only explicitly configured server IDs, local
transport, safe executable alias or endpoint host, supported protocol versions,
and namespaced tools. `AgentBus: Show MCP Server` displays configuration without
commands, environment values, or tokens. `AgentBus: Check MCP Server` performs
a bounded local diagnostic and displays negotiation, advertised tools, and
cleanup status.

Imported MCP calls use the Tool Invocations and Approvals views. The extension
cannot add a server or supply an arbitrary server command through the control
API. Configure servers in an explicit AgentBus config file and select it with
`agentbus.configPath`.

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
repository and the real local daemon. It completes a deterministic managed-tool
run, observes SSE and invocation transitions, approves and resumes an exact
tool call, opens safe artifacts and commit-backed native diffs, cancels an
active managed process and provider run, checks MCP diagnostics, and verifies
daemon restart recovery. The harness requires the daemon registry to be empty
at shutdown and every MCP fixture start marker to have a matching stop marker.
Provider credentials are removed from the test child environment.

Execution, resume, approval, and Git actions require VS Code Workspace Trust.
Multi-root workspaces always require an explicit repository selection.
Rejection, cancellation, and read-only doctor, tool, policy, MCP, and artifact
diagnostics remain available in untrusted workspaces.

Package locally with `npm run package`, then run `npm run package:audit`. The
audit rejects `.env` files, credentials, databases, bytecode, logs,
`node_modules`, nested VSIX files, and runtime state from the archive. Packaging
does not publish the extension.
