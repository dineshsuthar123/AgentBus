# Final release decision

The [release blocker follow-up](blocker-resolution.md) records later work and
its own validation evidence. This report covers the historical source digest
below.

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

**NOT READY — BLOCKED.** Do not distribute this dirty candidate to external pilot
users. No artifact was pushed, published or deployed.

| Acceptance gate | Decision |
| --- | --- |
| All mandatory CI on exact candidate commit | BLOCKED: no candidate commit/push; old remote CI is insufficient |
| Known Windows control failure | Locally repaired and repeatedly validated; protected candidate CI still required |
| No open critical/high security issue | FAIL: unresolved high picomatch advisory cascades through both Node toolchains |
| Required Python/integration/installed/browser gates | Python PASS; browser PASS; package PASS; limitations recorded |
| Supported restart / unauthorized denial / exact approvals | PASS scoped offline/native evidence; uncertain effects remain fail-closed |
| Actual repair database and parallel isolation | PASS disposable reference workflows only |
| Defined soak / environment-specific broad benchmarks | FAIL one-hour cycle 7 write; PASS 10 min and 11 broad benchmark metrics |
| Fresh installation / cross-client consistency | Actual installed and same-run Electron/browser/CLI evidence recorded |
| Honest documentation and synthetic/live distinction | This report explicitly retains failures, skips and unexecuted platforms/providers |

Implementation corrected owner cleanup and callback privacy, bounded admission,
cold imports, configuration parity, a narrow crash-recovery boundary, generated
evaluation fixtures and mobile browser defects. Existing customer-repair work
was preserved and tested rather than presented as newly authored here.

Remote `main` protection is now configured with 40 named required status checks,
strict up-to-date status, one approving review, stale-review dismissal,
last-push approval, conversation resolution and administrator enforcement;
force pushes/deletions are disabled. This was explicitly authorized in the
request. Repository evidence is `branch-protection-request.json` and
`branch-protection-result.json`. No branch content was pushed or merged.

To reconsider readiness: diagnose the one-hour managed-write failure with retained
owned state and repeat that full duration successfully; resolve the high advisory without suppressing it;
close any failing final browser/package/full-suite gate; review all intended
new and pre-existing files into a concrete candidate commit; rerun the complete
protected workflow against that exact commit; retain fresh hashes and evidence.
The 1200-second aggregate timeout must be understood under the intended CI
environment and not erased by calling a separate diagnostic pass the same gate.
Keep hosted execution disabled. Optional live-provider qualification needs its
own explicit authorization and bounded spend; it is not required for the honest
offline local scope.

The two founders can review and reproduce these local artifacts now. They should
not hand them to external pilots until every mandatory blocker is closed. A
scoped local pilot decision never implies unattended/public/multi-tenant readiness.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `release-check-full` | 2026-11-17T20:16:20 | 2026-11-17T20:47:17 | 1 | 1856.922 |
| `pytest-release-final` | 2026-11-17T21:12:58 | 2026-11-17T21:32:10 | 0 | 1152.547 |
| `control-release-final` | 2026-11-17T21:23:24 | 2026-11-17T21:25:31 | 0 | 126.578 |
| `package-audit-resumed` | 2026-11-18T08:22:00 | 2026-11-18T08:22:03 | 0 | 2.438 |
| `security-resumed` | 2026-11-18T08:22:05 | 2026-11-18T08:22:11 | 0 | 6.734 |
| `studio-audit-resumed` | 2026-11-18T08:17:02 | 2026-11-18T08:17:10 | 1 | 8.266 |
| `vscode-audit-resumed` | 2026-11-18T08:17:02 | 2026-11-18T08:17:11 | 1 | 8.594 |
| `soak-extended` | 2026-11-17T20:03:26 | 2026-11-17T21:03:44 | 1 | 3617.891 |
| `branch-protection-final-read` | 2026-11-17T21:31:21 | 2026-11-17T21:31:22 | 0 | 0.813 |
| `final-documentation-check` | 2026-11-18T08:14:06 | 2026-11-18T08:14:17 | 0 | 11.109 |
