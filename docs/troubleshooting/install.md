# Installation troubleshooting

## Confirm the interpreter

```console
python --version
python -m pip --version
python -m pip show agentbus
python -m agentbus.cli version --json
```

Supported Python versions are 3.11 through 3.14. Use `python -m pip` from the
same environment that will run AgentBus.

## Missing optional modules

- Control plane or VS Code: install `agentbus[ide]`.
- Azure: install `agentbus[azure]`.
- Azure identity: install `agentbus[entra]`.
- HTTP MCP: install `agentbus[mcp]`.
- Contributor tests/builds: install `agentbus[dev]`.

AgentBus will not install system Git, VS Code, Ollama, or model weights.

## Diagnose safely

```console
agentbus doctor --provider deterministic --verbose --json
agentbus config paths --json
agentbus upgrade-check --json
```

Do not paste `.env`, database files, full private paths, prompts, source, or
unredacted logs into an issue. Use `agentbus support-bundle` for bounded,
sanitized metadata after reviewing the archive locally.
