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

Execution and Git actions require VS Code Workspace Trust. Multi-root workspaces
always require an explicit repository selection. Read-only doctor diagnostics
remain available in untrusted workspaces.
