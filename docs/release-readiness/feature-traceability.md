# Feature-to-evidence traceability

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

| Feature / expected behavior | Implementation | Persistence / contract | Executed coverage | Status and limit |
| --- | --- | --- | --- | --- |
| CLI / installed first run | agentbus/cli.py; agentbus/product/quickstart.py | Shared durable state | Fresh-wheel quickstart and product/RC acceptance | PASS locally |
| Durable DAG / transitions / retry | agentbus/execution/engine.py; agentbus/runtime/durable_workflow.py | Tasks, attempts, immutable baselines | Whole Python suite, retry feedback, actual process loss | PASS scoped recovery; full suite gate below |
| Bounded parallel edits / isolation | agentbus/execution/scheduler.py; agentbus/worktrees/manager.py | Leases, worktree records, Git refs | Parallel/fencing/recovery tests and 9/9 core fake-provider evaluation | PASS offline |
| Cancellation / owner release | agentbus/control/supervisor.py | Durable cancellation + exact active operation | Native repeated control acceptance and real MCP finalizer failure | PASS locally; candidate CI BLOCKED |
| Approval / policy | agentbus/policy/; agentbus/tools/dispatcher.py | Revision/hash-bound approval records | Negative policy tests, mutation probes, real cross-client approval | PASS offline |
| Filesystem containment | agentbus/tools/filesystem_security.py | Canonical workspace and capability scope | Security/property/fuzz/native boundary checks | PASS scoped Windows; privilege skips disclosed |
| Managed process lifecycle | agentbus/sandbox/ | Catalog, Job Objects, budgets, cancellation | Process/stress/canary and real control acceptance | PASS Windows; not general OS sandbox |
| Verification / review / publication | agentbus/runtime/verifier.py; agentbus/runtime/orchestrator.py | Check/review evidence and Git reports | Durable retry/review, rejected publication, actual quickstart | PASS offline |
| REST / SSE / admission | agentbus/control/app.py; agentbus/control/admission.py | Shared SQLite/events/cursors | Auth/Origin/CSRF/limits tests; actual10/50/100 read/SSE clients | PASS bounded probe |
| Studio bootstrap/session | agentbus/control/studio.py; agentbus/product/daemon.py | In-memory ticket/session digests | Installed browser and security tests | PASS locally |
| Studio compose/history/diff/checks/evidence | studio/src/App.tsx; studio/src/pages/ | Control routes and persisted history | 203 component tests; full folio browser matrix | PARTIAL: final engine gates govern |
| Studio appearance/accessibility/reflow | studio/src/kinetic.css; studio/src/pages/repair.css | User theme, DOM state | Axe/keyboard/reflow across four engines | PARTIAL: no manual screen-reader certification |
| VS Code controls / trust / recovery | extensions/vscode/src/extension.ts | Bearer control API / SecretStorage | 104 unit tests, actual Electron,5 fresh-profile stages | PASS Windows |
| CLI–Studio–IDE state consistency | extensions/vscode/src/test/suite/index.ts; studio/scripts/cross-client-smoke.mjs | Same run/approval/attempt state | Real CLI create, Studio inspect, Electron approve/resume, CLI verify | PASS observed same run |
| Repository index/search/impact | agentbus/intelligence/ | Local SQLite index and lease | 4/4 intelligence evaluation;1k/10k ten-scenario scale runs | PASS correctness; slow10k updates |
| Trace/provenance/replay | agentbus/trace/; agentbus/replay/ | Content-addressed objects, seals and events | Tamper/property/import tests and real daemon replay | PASS offline |
| Synthetic customer repair | agentbus/repair/; agentbus/control/repair.py; studio/src/pages/RepairPage.tsx | Owned SQLite app + repair journal | Actual database cohort oracle and 13-state browser journeys | PASS local fixture only |
| Read-only imported evidence | agentbus/repair/service.py | Imported snapshots and safe case scopes | Repair/import scope and mutation-denial tests | PASS offline |
| Connected application-authoritative repair | agentbus/repair/integration/; agentbus/control/connected_repair.py | Separate HTTP/PostgreSQL app + conditional revision | 39 actual PostgreSQL tests and installed canary/lost-response acceptance | PASS reference; EXPERIMENTAL externally |
| Payment provider evidence | agentbus/repair/live/ | External evidence adapter / restricted credential | Offline contracts only | Live NOT_RUN; no payment-write claim |
| Deterministic model adapter | agentbus/models/deterministic.py; agentbus/configuration.py | Fixture profiles and safe model routing | Strict config/provider tests, real offline runs | PASS; not LLM quality |
| Ollama / Azure adapters | agentbus/models/ | Configured endpoint/schema/error contract | Offline timeout/refusal/malformed response tests | Live NOT_RUN, optional |
| MCP stdio / HTTP / lifecycle | agentbus/mcp/ | Configured allowlist, managed invocations, exact approvals | Hostile peer/robustness tests and real local MCP acceptance | PASS controlled peers only |
| Packaging / supply chain | agentbus/release_packaging.py; agentbus/release_security.py | Wheel/sdist/VSIX manifests and digests | Final serial audit/install; Python/Node scanners | BLOCKED by Node high advisory |
| Reliability / soak / performance | agentbus/product/soak.py; agentbus/product/benchmark.py | Owned disposable state and resources | 15 reliability cases,10 min/1 h profiles,11 latency metrics | PASS 10 min and benchmarks; FAIL 1 h; limits recorded |
| Hosted execution | docs/product/limitations.md | Independent tenant/OS/network authority required | No hosted deployment performed | UNSUPPORTED |

A PASS is scoped to the cited executed boundary. Complete release acceptance remains BLOCKED. Do not infer paid-provider or production-customer results from fixture coverage.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `core-eval` | 2026-11-17T19:34:51 | 2026-11-17T19:35:49 | 0 | 57.5 |
| `intelligence-eval-fixed` | 2026-11-17T19:42:59 | 2026-11-17T19:43:08 | 0 | 9.703 |
| `process-recovery-regression` | 2026-11-17T20:46:16 | 2026-11-17T20:48:49 | 0 | 152.078 |
| `pytest-complete-candidate` | 2026-11-17T20:47:58 | 2026-11-17T21:08:45 | 1 | 1246.891 |
| `pytest-release-final` | 2026-11-17T21:12:58 | 2026-11-17T21:32:10 | 0 | 1152.547 |
