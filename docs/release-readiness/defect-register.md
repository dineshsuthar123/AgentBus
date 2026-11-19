# Defects, regressions and unresolved gates

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

| ID / priority | Reproduction and cause | Change / regression evidence | Disposition |
| --- | --- | --- | --- |
| D01 / P0 | Finalizer raises after worker completion; `_completed` never released active/workspace owner; resume remains 409 | `control/supervisor.py` releases the exact operation in `finally`, observes worker failures and records safe error categories; stale callbacks cannot release a newer owner. Supervisor tests and real MCP approval/finalizer injection | FIXED locally; exact-candidate Windows CI still required |
| D02 / P1 | Background run requests and SSE/REST work were insufficiently bounded | Supervisor rejects work beyond capacity; separate REST/SSE slots and token bucket; cancellation/error paths release slots | FIXED; tests and authenticated 10/50/100-client load |
| D03 / P1 | Cold `import agentbus.policy` failed through tools→execution→partially initialized policy | Type-only CancellationToken import; three cold-interpreter import regressions | FIXED |
| D04 / P1 | CLI ignored deterministic environment profile while other config readers honored it; cross-client run used calculator instead of approval fixture | Canonical/legacy profile, latency and failure mappings with strict CSV/positive integer validation | FIXED; red/green config tests and real CLI/Studio/Electron journey |
| D05 / P1 | Hard kill before first tool call marked attempt interrupted, then retry failed because no pre-crash corrective evidence existed | Engine reconstructs explicitly marked post-interruption evidence only before any task tool invocation; executor requires exact checkpointed baseline. Drift denies; attempts remain bounded | FIXED for this boundary; partial/ambiguous effects remain fail-closed |
| D06 / P2 | Intelligence evaluator expected ignored generated bundle missing from source fixture | Generate bundle only inside the owned copied fixture; real index must exclude it; source fixture untouched | FIXED, 4/4 evaluation cases |
| D07 / P2 | WebKit native select intrinsic width overflowed mobile working set/import fields | Contained layout, min-inline-size and preserved focus padding; caption foreground explicit | Repair journey passed; folio result in browser matrix |
| D08 / P2 | WebKit theme switches retained inherited foreground in full journey | Explicit selected-theme foreground binding; exact accessibility scans retained | Final result in browser matrix; unresolved failures remain blocking |
| D09 / P2 | Folio smoke captured position before actionability scrolling; persistent shelf duplicated a global history selector | Capture after explicit link scroll, scope selector to history; keep 1px restoration and all functional assertions | Test corrected; failures retained |
| D10 / P2 | Windows venv redirector hit zero-child Job Object budget before Python startup; test only recognized English quota wording | Independent child side-effect canary plus exact Windows exit101/prefix case; limit remains enforced, no skip | Native 3.11/3.12 quota/stress checks pass |
| D11 / P2 | Pre-existing VS Code download lacked native policy-watcher module | Allow a task-owned cache; download fresh pinned VS Code 1.96.4 | Real integration and five-stage fresh-profile checks pass; old cache preserved |
| D12 / P2 | Concurrent packaging invocations raced on shared build files | Stop concurrent builds; serial rebuild and independent semantic comparison | Failed attempt disclosed; final package audit governs |
| B01 / P1 | `npm audit --json` in Studio or extension reports high picomatch GHSA-fc5q-39xc-vjpf, no patched range available at audit time | Compatible `npm audit fix` applied; no force/downgrade/suppression | OPEN RELEASE BLOCKER |
| B02 / P0 gate | Candidate includes dirty and untracked source; remote CI only covers old HEAD | Candidate commit and all 40 mandatory protected checks are required | BLOCKED; user prohibited push/merge/publication |
| B03 / P1 gate | `release-check --full --json`: full pytest hit its 1200s command budget; concurrent source changes caused two package reproducibility findings | Standalone final whole-suite run and serial package comparison recorded separately | Original aggregate remains FAIL; do not relabel it PASS |
| B04 / P1 | One-hour seed 2026/parallelism 2 soak failed cycle 7's managed filesystem write; 965 other cycles completed the managed lifecycle | Original temporary state was not retained, so the underlying error category is unavailable. A bounded observation-only diagnostic repeat is recorded separately | OPEN reliability blocker; a shorter pass cannot clear it |
| D13 / P1 test safety | Windows `os.kill(pid,0)` used as a non-mutating MCP liveness probe actually calls TerminateProcess; full run reported peer still alive | Read-only GetExitCodeProcess query; a live stdin/stdout peer proves the probe leaves it running; 29 focused MCP/process tests pass | FIXED observer; historical full failure remains recorded |
| D14 / P2 build procedure | Direct `build --wheel` reused dirty build/lib and included stale Studio chunks/fixture bytecode | The supported release procedure builds an sdist and a wheel from its fresh extracted source; final selected artifacts pass exact asset hygiene and semantic comparison | Dirty direct-wheel build is not qualified; failure retained |

Reproduce D01 with `pytest tests/test_control_supervisor.py
tests/test_control_mcp_owner_recovery.py`; before the fix the injected finalizer
strands the owner. Reproduce D05 with `pytest tests/test_control_process_restart.py`;
`active_task` expects successful retry, `drifted_task` expects zero tool calls and
failure, `exhausted_task` preserves completed step1, `pending_approval` executes
one approved deletion. Force termination targets only Popen-owned test trees.

For B01, use `npm ci` then `npm audit --audit-level=high` in both Node projects.
Adopt a verified upstream patch or reviewed replacement, then repeat compile,
unit, browser/Electron and package gates. No security waiver was invented.
For B02, review/include the intended source, create a candidate commit and run
the protected workflow. This session deliberately does not push or manufacture
a successful check on the dirty tree.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `owner-regression-red` | 2026-11-17T19:30:22 | 2026-11-17T19:30:25 | 1 | 3.093 |
| `owner-regression-green` | 2026-11-17T19:30:52 | 2026-11-17T19:30:55 | 0 | 3.641 |
| `policy-cold-import-red` | 2026-11-17T19:57:32 | 2026-11-17T19:57:32 | 1 | 0.609 |
| `policy-cold-import-green` | 2026-11-17T19:58:09 | 2026-11-17T19:58:13 | 0 | 4.171 |
| `deterministic-env-red` | 2026-11-17T20:12:19 | 2026-11-17T20:12:21 | 1 | 1.781 |
| `deterministic-env-green` | 2026-11-17T20:12:50 | 2026-11-17T20:12:53 | 0 | 3.172 |
| `process-recovery-regression` | 2026-11-17T20:46:16 | 2026-11-17T20:48:49 | 0 | 152.078 |
| `release-check-full` | 2026-11-17T20:16:20 | 2026-11-17T20:47:17 | 1 | 1856.922 |
