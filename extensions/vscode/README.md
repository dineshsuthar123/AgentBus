# AgentBus for VS Code

Native VS Code views and commands for the authenticated, loopback-only AgentBus
control plane. The extension uses Workspace Trust, stores daemon tokens only in
VS Code SecretStorage, and opens repository changes with native diff editors.
It displays managed tool calls, exact policy approvals, bounded artifacts,
resource and cancellation state, and explicitly configured local MCP servers.

Install the Python control plane first:

```text
pip install "agentbus[ide]"
```

Then verify and package locally:

```text
npm ci
npm run protocol:check
npm run compile
npm run lint
npm test
npm run package
npm run package:audit
```

Install the resulting VSIX manually. Packaging does not publish it. Execution,
approval, and Git operations require Workspace Trust. Read-only tool, policy,
MCP, artifact, and doctor diagnostics remain available without trust.

See `../../docs/vscode-extension.md`, `../../docs/tool-runtime.md`, and
`../../docs/mcp-integration.md` for the complete behavior and security model.
