# Install AgentBus

## Requirements

- Python 3.11, 3.12, 3.13, or 3.14
- Git available on `PATH`
- Windows or Linux for the tested public-beta paths
- Node.js 22 only when building the VS Code extension

AgentBus never installs Git, VS Code, Ollama, model weights, or system packages
for you.

## Choose dependencies

| Install | Use |
| --- | --- |
| `agentbus` | CLI, deterministic provider, Ollama HTTP adapter, durable runtime, tools, replay, and repository intelligence |
| `agentbus[ide]` | Local FastAPI control plane and VS Code integration |
| `agentbus[azure]` | Azure OpenAI provider |
| `agentbus[entra]` | Optional Azure identity support |
| `agentbus[mcp]` | HTTP MCP client support |
| `agentbus[all]` | All runtime integrations |
| `agentbus[dev]` | Tests and package-building tools for contributors |

From a source checkout:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[ide]"
```

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[ide]'
```

For a non-editable install from a checkout, omit `-e`. Release artifacts can
be built with `python -m build`, audited, and installed from the resulting
wheel. Do not use an editable install for clean-install acceptance.

## Verify locally

```console
agentbus version --json
agentbus doctor --provider deterministic --json
```

`doctor` is offline unless `--live-provider` is supplied explicitly. Warnings
for unconfigured Azure, Ollama, MCP, or evaluation baselines are expected when
those optional features are not in use.

## Upgrade an existing installation

Before changing package versions, stop local daemons and inspect compatibility:

```console
agentbus daemon status --json
agentbus upgrade-check --workspace . --json
agentbus migrate status --json
```

Apply a migration only after reviewing its plan. AgentBus creates bounded
backups for supported migrations and never performs schema downgrades.

Continue with the [quickstart](quickstart.md). For failures, see
[installation troubleshooting](../troubleshooting/install.md).
