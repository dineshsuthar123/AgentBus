# Executed validation matrix

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

Whole-suite final status: **PASS**. Observed summary: `2182 passed, 11 skipped, 2 warnings in 1147.46s (0:19:07)`.
Do not add overlapping focused runs to this count. Initial 3.14 baseline was
3 failed/2149 passed/11 skipped; the 3.12 coverage run was 1 failed/2165 passed/11 skipped
(native quota diagnostic test, subsequently corrected with an independent child
canary). Neither failed full run is relabeled PASS.

The next whole-suite run recorded 1 failed/2180 passed/11 skipped: its only failure
was the MCP cleanup observer. That observer used destructive Windows signal 0
probing; the read-only correction and live-peer regression passed 29 focused
checks. The latest whole-suite result above is a distinct command, not a rewritten
older result. PostgreSQL integration was enabled for both whole-suite reruns.

The failed coverage run still measures 50,251 statements,42,979 covered
(85.53% line),14,268 branches,10,183 covered (71.37% branch),82.397% combined.
It is historical coverage evidence, not coverage for the final additional
recovery code. No 100% or whole-project mutation claim is made.

| Mandatory category | Status | Evidence / limits |
| --- | --- | --- |
| Exact-candidate CI | BLOCKED | Dirty tree; remote40374632610 has 37/38 old jobs successful and Windows control failure |
| Python deterministic/unit/integration | PASS | `pytest-release-final`, source tree; native Windows 3.12 |
| Property/fuzz/parser/protocol/adversarial | PASS via whole suite | Existing suites remain enabled; targeted policy mutations both killed |
| Windows known owner failure | PASS locally / CI BLOCKED | Repeated actual control acceptance plus finalizer-failure real MCP regression |
| Real hard-kill/restart state | PASS scoped | Four installed-daemon scenarios, no duplicated terminal effects |
| Security authorization/containment | PASS scoped | Negative policy/path/Origin/CSRF/MCP/process tests; OS skips disclosed |
| Dependency security | FAIL | Studio 12 high; extension 14 high+2 moderate; Python/runtime-only trees clear |
| Studio unit/type/lint/build | PASS | 203 tests/48 files, actual compiler/linter/build |
| Required browser E2E | PASS | Final per-engine folio/repair exits; intermediate failures remain documented |
| VS Code unit/Electron/fresh-profile | PASS locally | 104 unit; actual extension host;5-stage recovery/trust product acceptance |
| Customer repair actual database | PASS reference | SQLite independent cohort and actual PostgreSQL18 canary; no real customers |
| Parallel isolation / publication gates | PASS offline | Worktree/fencing/recovery, core evaluation 9/9, verifier/reviewer gates |
| Offline evaluation | PASS | Core 9/9,36 fake requests 432 fake tokens 1 retry; intelligence 4/4 with 0 provider calls |
| Reliability / soak | FAIL extended; PASS 10 min | 15 scenarios,10 min defined profile; one-hour cycle 7 write failure remains open |
| Performance budgets | PASS broad 11 metrics | Same-host baseline comparison;10k latency is a disclosed limit |
| Installed artifact / reproducibility | PASS | Fresh isolated origin and actual quickstart; semantic comparison not byte guarantee |
| Cross-client state consistency | PASS observed | Same CLI-created run in Studio and Electron; CLI final status confirms |
| Native Linux/macOS | NOT_RUN locally | Historical CI is not candidate evidence; configured native platform CI required |
| Live providers/payments | NOT_RUN, optional | Authorization absent; no cost or production mutation performed |

Full aggregate `release-check --full --json` exited1:21 passed,3 failed,1warning.
Failures were dirty Git state,1200-second whole-suite timeout, and two package
reproducibility differences while source/builds changed. The separate longer
diagnostic whole-suite run does not change that aggregate exit. Original command
budgets and strict assertions remain unchanged. No skip is counted as a pass.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `pytest-baseline` | 2026-11-17T19:29:10 | 2026-11-17T19:46:32 | 1 | 1042.047 |
| `pytest-final-312` | 2026-11-17T19:50:16 | 2026-11-17T20:13:54 | 1 | 1418.203 |
| `pytest-complete-candidate` | 2026-11-17T20:47:58 | 2026-11-17T21:08:45 | 1 | 1246.891 |
| `pytest-release-final` | 2026-11-17T21:12:58 | 2026-11-17T21:32:10 | 0 | 1152.547 |
| `mcp-observer-regression` | 2026-11-17T21:12:04 | 2026-11-17T21:12:10 | 0 | 5.891 |
| `final-source-regressions` | 2026-11-17T20:26:10 | 2026-11-17T20:26:59 | 0 | 48.703 |
| `process-recovery-regression` | 2026-11-17T20:46:16 | 2026-11-17T20:48:49 | 0 | 152.078 |
| `studio-unit-candidate` | 2026-11-17T20:18:10 | 2026-11-17T20:21:12 | 0 | 181.75 |
| `vscode-unit` | 2026-11-17T19:39:12 | 2026-11-17T19:39:17 | 0 | 4.813 |
| `release-check-full` | 2026-11-17T20:16:20 | 2026-11-17T20:47:17 | 1 | 1856.922 |
