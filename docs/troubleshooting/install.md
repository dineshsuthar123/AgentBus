# Installation troubleshooting

## Confirm the interpreter

```console
python --version
python -m pip --version
python -m pip show syndra
python -m syndra.cli version --json
```

Supported Python versions are 3.11 through 3.14. Use `python -m pip` from the
same environment that will run Syndra.

## Missing optional modules

- Control plane or VS Code: install `syndra[ide]`.
- Azure: install `syndra[azure]`.
- Azure identity: install `syndra[entra]`.
- HTTP MCP: install `syndra[mcp]`.
- Contributor tests/builds: install `syndra[dev]`.

Syndra will not install system Git, VS Code, Ollama, or model weights.

## Diagnose safely

```console
syndra doctor --provider deterministic --verbose --json
syndra config paths --json
syndra upgrade-check --json
```

Do not paste `.env`, database files, full private paths, prompts, source, or
unredacted logs into an issue. Use `syndra support-bundle` for bounded,
sanitized metadata after reviewing the archive locally.
