# Contributing to AgentBus

Thanks for helping make AgentBus safer and easier to use. Keep changes focused,
add regression coverage, and preserve the conservative execution boundaries.

## Development setup

PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[azure,dev]"
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m agentbus.eval run --suite core-offline --variant durable-parallel-fake
```

POSIX shell:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[azure,dev]'
.venv/bin/python -m pytest
.venv/bin/python -m agentbus.eval run --suite core-offline --variant durable-parallel-fake
```

Normal tests must remain offline. Never commit `.env`, credentials, state
databases, run logs, benchmark clones, caches, or provider responses. Live
checks require explicit flags and should use the smallest practical budgets.

## Pull requests

- Explain the behavioral and safety impact.
- Add tests for new behavior and failure paths.
- Run `python -m compileall agentbus`, full pytest, the core offline suite, and
  `git diff --check`.
- Do not weaken `shell=False`, workspace boundaries, approval gates, final
  review, redaction, or explicit consent requirements.
- Report generated files and retained side effects honestly.

See [SECURITY.md](SECURITY.md) for vulnerabilities and
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) for release work.
