# AgentBus for VS Code

Native VS Code views and commands for the authenticated, loopback-only AgentBus
control plane. The extension uses Workspace Trust, stores daemon tokens only in
VS Code SecretStorage, and opens repository changes with native diff editors.

Install the Python control plane first:

```text
pip install "agentbus[ide]"
```

Then package locally with `npm run package` and install the resulting VSIX.
