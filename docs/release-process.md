# Release process

The authoritative version is `agentbus/_version.py`. Packaging reads that
attribute dynamically, and CLI, durable run metadata, evaluation reports, and
release reports import the same value.

## Verify

PowerShell:

```powershell
.venv\Scripts\python.exe -m compileall agentbus
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m agentbus.eval run --suite release-offline --variant durable-parallel-fake
.venv\Scripts\python.exe -m build
git diff --check
```

POSIX:

```bash
.venv/bin/python -m compileall agentbus
.venv/bin/python -m pytest
.venv/bin/python -m agentbus.eval run --suite release-offline --variant durable-parallel-fake
.venv/bin/python -m build
git diff --check
```

Run the release suite twice and compare semantic fields: case IDs, pass/fail,
scores, assertions, changed-file scope, safety failures, retries, fallbacks,
reviewer outcome, and verifier outcome. Run IDs, timestamps, temporary paths,
Git commit IDs created inside fixtures, and measured durations are expected to
differ.

Install the built wheel in a newly created virtual environment, not an editable
checkout. Change directory outside the repository and verify both entry points,
offline doctor, and the release suite. This proves there is no repository-root
`PYTHONPATH` assumption.

Generate evidence JSON for tests and install smoke, then run:

```powershell
agentbus release-report --offline-run RUN_ID --test-evidence TEST.json --install-evidence INSTALL.json --markdown-output REPORT.md --json-output REPORT.json --check
```

The report reads actual build artifacts, evaluation storage, Git state, schema,
and doctor output. Omitted evidence remains `NOT_RUN`.

Follow [RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md). Live Azure acceptance
is manual and optional for a local alpha build; it must never run on pull
requests. Tagging, PyPI publication, and GitHub release creation require a
separate maintainer decision.
