# VS Code onboarding

The native extension connects only to authenticated loopback AgentBus daemons.
It uses VS Code SecretStorage for bearer tokens and requires Workspace Trust
before execution, approval, or Git mutation.

## Install the local control-plane dependencies

```console
python -m pip install "agentbus[ide]"
agentbus version --json
```

For extension development, build a private VSIX without publishing it:

```console
cd extensions/vscode
npm ci
npm run package
npm run package:audit
```

Install the resulting `agentbus-vscode.vsix` with VS Code's **Install from
VSIX** command. The package audit rejects source files, credentials, runtime
state, maps, tests, and oversized contents.

## First activation

The extension shows onboarding once for each supported AgentBus minor line. It
checks package, control-protocol, and state-schema compatibility before using a
daemon. Start with these commands from the Command Palette:

1. `AgentBus: Get Started`
2. `AgentBus: Check Installation`
3. `AgentBus: Run Setup`
4. `AgentBus: Run Quickstart`
5. `AgentBus: Open Resolved Configuration`

Setup and quickstart default to the deterministic provider and do not require
Azure, Ollama, or credentials.

## Settings

`agentbus.pythonPath`, `agentbus.executablePath`, `agentbus.configPath`, and
`agentbus.registryPath` are machine-scoped. Prefer an explicit path to the
Python environment where AgentBus is installed. Provider secrets are not VS
Code settings.

The extension will not auto-start a daemon in an untrusted workspace. Trust is
not a promise that repository content is safe; it is an explicit VS Code gate
before AgentBus can execute.

## Daily flow

Use the AgentBus activity bar to start or attach the daemon, build an index,
submit a task, inspect task and tool events, approve an exact risky operation,
open native diffs, review the final report, and replay a completed run offline.

See [daemon troubleshooting](../troubleshooting/daemon.md) and the detailed
[extension reference](../vscode-extension.md).
