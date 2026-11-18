# Native platform and browser coverage

Evidence captured 2026-11-18T08:24:39.122835+00:00. Commit `3a36800fde7d213af5f742da816574612a3e78cd` plus the preserved dirty tree on
`feature/release-hardening-20261118`; this is **not a committed release candidate**.
Source digest: `45c66d3cf94db7aaf5b98a72b73ee141b1ea5263ee14f13a4a2fe19fd2c58996` (per-file inventory and dependency
versions: `.tmp/release-readiness/20261118/candidate-complete.json`). Native Windows 11
build 26200, AMD64, 16 logical processors, 15.3 GiB RAM; Python 3.12.10,
Node 22.17.0, npm 10.9.2, FastAPI 0.163.2, Pydantic 2.14.3, pytest 9.2.0.
Vite 8.3.8 and VS Code 1.96.4 were used. Command timestamps are UTC with full
dates in metadata and tables. The main validation campaign ran on 2026-11-17 UTC;
report finalization continued on 2026-11-18 UTC (2026-11-18 in Asia/Calcutta).
The digest covers tracked files plus scoped new implementation, tests, config and
product docs; generated Studio assets and these self-referential audit reports
are excluded. The Studio manifest and artifact hashes cover those generated assets.
Per-interpreter dependency/origin inventories and browser versions are in
`.tmp/release-readiness/20261118/environment-inventory.json` (Edge 157.0.4341.60).

[Evidence ledger](evidence-ledger.md) records commands, start/end times,
exit status and log SHA-256 references. Each prefix refers to ignored local
`<prefix>.json` metadata and `<prefix>.log` under `.tmp/release-readiness/20261118/`.
Metadata preserves exact invocations; repository-specific API targets are
abbreviated in the public command ledger.
`candidate-resumed.json` confirms the source, branch and HEAD were unchanged
after the interruption. Dependency audits were refreshed on 2026-11-18 UTC.
The only subsequent source edit removed one trailing blank line from
`tests/test_product_config.py`; runtime code is unchanged and the sdist was rebuilt.
The ledger includes failed runs; later passes do not erase them. Overlapping tests
are never added together as independent coverage. No paid inference, production
customer mutation, publication or deployment was performed.

Only native Windows was available for the recorded runs. WSL was inventoried but
not used as a substitute for native Linux. Native Linux and macOS are **NOT_RUN**.
The historical Linux CI results cover the old commit, not this implementation.

| Runtime / surface | Actual result | Limits |
| --- | --- | --- |
| CPython 3.12.10 Windows | Full suite and installed-wheel journeys recorded in test/E2E matrix | Final exact commit CI still absent |
| CPython 3.11.9 Windows | Final wheel/quickstart and 92 boundary tests pass; original run90pass/1quota failure and 24-test quota/stress follow-up retained | No whole3.11 suite claim |
| CPython 3.13.7 Windows | Final wheel/quickstart and 92 boundary tests pass | No whole-suite3.13 claim |
| CPython 3.14.6 Windows | Final wheel/quickstart and 92 boundary tests pass | Initial whole suite2149pass/3fail/11skip before fixes |
| Chromium151.0.7922.34 / Firefox 153 / WebKit 26.5 / installed Edge | Real daemon browser journeys; final result in E2E report | Windows engine coverage does not establish Safari/macOS support |
| VS Code 1.96.4 Windows Electron | Unit104, real integration and five-stage fresh-profile product acceptance pass | Host emits native mutex/N-API/proposed API warnings; not zero host logs |

The existing `.venv` was Python 3.14.6 with broken pip internals. Fresh task-owned
environments were used; the user's environment/cache was not repaired or deleted.
Windows 3.11/3.12 venv launchers can be denied by a zero-child Job Object budget
before interpreter startup. A test-only child marker verifies that no child effect
escaped. The process budget itself was not relaxed.

Eleven whole-suite skips are OS/privilege dependent; consult JUnit and pytest
output. Conditional real PostgreSQL tests were explicitly enabled using the local
PostgreSQL18 binaries; they were not silently counted as skipped successes.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `python-3.11-origin` | 2026-11-17T20:09:30 | 2026-11-17T20:09:31 | 0 | 1.125 |
| `python-3.11-quickstart-final` | 2026-11-17T21:09:02 | 2026-11-17T21:09:07 | 0 | 4.75 |
| `python-3.11-core-boundaries` | 2026-11-17T20:09:37 | 2026-11-17T20:09:51 | 1 | 14.125 |
| `native-quota-regression-311` | 2026-11-17T20:15:38 | 2026-11-17T20:15:45 | 0 | 6.515 |
| `python-3.11-boundaries-final` | 2026-11-17T21:09:07 | 2026-11-17T21:09:21 | 0 | 13.375 |
| `python-3.13-origin` | 2026-11-17T20:09:29 | 2026-11-17T20:09:30 | 0 | 1.188 |
| `python-3.13-quickstart-final` | 2026-11-17T21:09:02 | 2026-11-17T21:09:07 | 0 | 4.797 |
| `python-3.13-boundaries-final` | 2026-11-17T21:09:07 | 2026-11-17T21:09:21 | 0 | 13.375 |
| `python-3.14-origin` | 2026-11-17T20:09:28 | 2026-11-17T20:09:30 | 0 | 1.437 |
| `python-3.14-quickstart-final` | 2026-11-17T21:09:02 | 2026-11-17T21:09:07 | 0 | 5.016 |
| `python-3.14-boundaries-final` | 2026-11-17T21:09:07 | 2026-11-17T21:09:21 | 0 | 13.375 |
