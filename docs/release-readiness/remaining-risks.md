# Remaining risks and operating limits

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

Do not hand this candidate to external pilots yet. Dependency-security, extended
soak and exact candidate CI gates are still open; a positive local demonstration cannot override
them. Reproduction and remediation are in [the defect register](defect-register.md).

| Risk / gate | Status | Required next evidence |
| --- | --- | --- |
| High toolchain dependency advisory | FAIL / blocking | Verified patch or reviewed replacement; full audits and Node gates |
| Exact final commit and protected CI | BLOCKED | Intentional review of preserved/new files; candidate commit; all 40 checks and review |
| Aggregate release-check | FAIL | Clean, stable source and completed bounded suite/package comparison; original result retained |
| Extended soak filesystem write | FAIL / blocking | Reproduce cycle 7 seed 2026/parallelism 2 with retained owned state and error category; diagnose and repeat full duration |
| Linux/macOS native coverage | NOT_RUN | Mandatory configured platform CI; macOS only if declared supported |
| Real-model quality and spend controls | NOT_RUN, optional | Explicit authorization, bounded credentials and live quality/cost fixtures |
| Real customer payment/access repair | NOT_RUN, experimental | Customer-authoritative adapter contract, authorized staging race/fault tests, operational ownership |
| General accessibility | Limited | Recorded axe/keyboard/reflow plus manual screen-reader work; browser engines are not physical assistive technology |
| Large scale / sustained API mutation load | Limited | Current1k/10k generated indexing and bounded read/SSE tests; no approval-write/artifact throughput capacity claim |
| Recovery after uncertain external effects | Fail-closed | Durable application idempotency or operator reconciliation; unchanged pre-tool task is the validated automatic retry boundary |
| Process/tenant/network containment | Unsupported for hosting | Independent OS isolation, egress limits and tenant identity/authorization |
| Retained index/history growth | Measured, not eliminated | Disk quotas, retention and long-horizon testing; documented DB growth is not labeled a memory leak |

Local pilot operation, after blockers close, requires a trusted OS account,
disposable or backed-up repositories, explicit small capability scopes, reviewed
approval effects, bounded concurrency and retained trace evidence. Keep hosted
execution disabled. Do not expose the loopback daemon via reverse proxy/tunnel.
Back up durable SQLite state using the supported coordinated procedure, not an
uncoordinated copy during writes; perform restore verification before relying on it.
The user-facing setup, recovery, backup and repair documents remain under
`docs/product/`; the release-specific evidence reports accompany the audit rather
than being embedded in the wheel/sdist and creating a self-hash cycle.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `release-check-full` | 2026-11-17T20:16:20 | 2026-11-17T20:47:17 | 1 | 1856.922 |
| `studio-audit-final` | 2026-11-17T20:43:13 | 2026-11-17T20:43:21 | 1 | 8.531 |
| `vscode-audit-final` | 2026-11-17T20:43:21 | 2026-11-17T20:43:27 | 1 | 5.187 |
