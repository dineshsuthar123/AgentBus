# Install Syndra

## Requirements

- Python 3.11, 3.12, 3.13, or 3.14
- Git available on `PATH`
- Windows or Linux for the tested public-beta paths
- Node.js 22 or newer only for contributors building Studio or the extension

Syndra never installs Git, VS Code, Ollama, model weights, or system packages
for you.

## Choose dependencies

| Install | Use |
| --- | --- |
| `syndra` | CLI, deterministic provider, Ollama HTTP adapter, durable runtime, tools, replay, and repository intelligence |
| `syndra[ide]` | Local FastAPI control plane and VS Code integration |
| `syndra[azure]` | Azure OpenAI provider |
| `syndra[entra]` | Optional Azure identity support |
| `syndra[mcp]` | HTTP MCP client support |
| `syndra[all]` | All runtime integrations |
| `syndra[dev]` | Tests and package-building tools for contributors |

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

## Prepared wheel

For an already prepared, audited local distribution:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install "dist/syndra-0.6.0b1-py3-none-any.whl[ide]"
.venv\Scripts\syndra.exe studio --workspace .
```

On Linux use `.venv/bin/python` and `.venv/bin/syndra`. The wheel contains
Studio's built assets and license notices; the end user needs no Node process.
Closing the browser does not stop the foreground daemon or cancel other clients'
runs. Use Ctrl+C in its owning terminal to stop it deliberately.

## Build from source

Editable Python installation alone does not build web assets. Contributors:

```powershell
cd studio
npm.cmd ci
npm.cmd run build
cd ..
.venv\Scripts\python.exe scripts/prepare_studio.py
.venv\Scripts\python.exe -m build
.venv\Scripts\python.exe scripts/verify_distribution.py dist
```

Use `npm` rather than `npm.cmd` on Linux. Build before packaging, so both wheel
and sdist include the prepared assets. Do not use an editable installation for
clean-install acceptance. See [Studio](../product/studio.md) for development
proxy and browser authentication details.

## Verify locally

```console
syndra version --json
syndra doctor --provider deterministic --json
```

`doctor` is offline unless `--live-provider` is supplied explicitly. Warnings
for unconfigured Azure, Ollama, MCP, or evaluation baselines are expected when
those optional features are not in use.

## Upgrade an existing installation

Before changing package versions, stop local daemons and inspect compatibility:

```console
syndra daemon status --json
syndra upgrade-check --workspace . --json
syndra migrate status --json
```

Apply a migration only after reviewing its plan. Syndra creates bounded
backups for supported migrations and never performs schema downgrades.

Continue with the [quickstart](quickstart.md). For failures, see
[installation troubleshooting](../troubleshooting/install.md).
