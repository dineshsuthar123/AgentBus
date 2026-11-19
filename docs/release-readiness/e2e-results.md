# Installed and cross-client end-to-end results

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

## Real journeys and independent state

1. Fresh wheel CLI quickstart created a disposable Git workspace and durable
   task, ran managed writes/tests, passed independent verification/review and
   retained inspectable evidence. Python 3.11/3.12/3.13/3.14 isolated import origins
   were checked; a checkout import was not substituted for installed execution.
2. Installed Studio authenticated via single-use launch URL, created an actual
   deterministic run, inspected checks/diff/review/trace/replay, navigated123
   persisted history rows and retained scroll/filter state. Synthetic history
   rows are explicitly marked persisted-only, not executed tasks. Keyboard,
   focus, reduced motion, narrow320px and 200%-equivalent reflow were exercised.
3. The same CLI-created approval run was inspected in a real Studio browser,
   approved/resumed inside actual VS Code Electron, then inspected again through
   Studio and CLI. `cross-client-passed/pending.json` and `completed.json` identify
   run `1ab320c4dd4047d89f0a1eb1c54bd371`: waiting_for_approval→succeeded with reviewer
   approved and zero browser errors. The approval capability envelope was visible.
4. Synthetic repair used an owned SQLite application:65 paid members,5 manual
   fixes,60 supported corrections, separate ineligible refund. Browser reload,
   dropped response and reconciliation preserved idempotent export/database state.
   The strengthened independent test adds5 support fixes and 1 refund after preview:
  59 new grants,6 skips, exactly5 already-correct; the original account cohort has
  79 allowed. Forward-bug probes before/after the fix are separate accounts.
5. Connected reference repair started a separate PostgreSQL18 application and
   installed daemon. Exact-plan approval applied one canary; response loss,
   reload/reconciliation preserved actual normal-app access `allowed=true`,
   `propagated=true`, revision2. Only one execute request occurred; five
   accessibility states, zero violations/browser errors/external requests.
   This proves the disposable reference application, not live Stripe/customers.
6. Real process loss/restart preserved pending exact approval and retried an
   unchanged pre-tool task with remaining budget. Drift ran no tool; exhausted
   task retained completed work. Repeated terminal resume created no new effect.

## Browser matrix

Initial actual wheel folio and repair journeys passed Chromium, Firefox and
installed Edge. WebKit exposed native-select reflow and dark-caption contrast.
Explicit live-theme foregrounds on text surfaces and default controls corrected
the inherited-color issue. Final per-engine results are below. Screenshots and accessibility JSON are owned
local evidence; axe does not establish full screen-reader conformance.

| Engine | Final folio gate | Final repair gate |
| --- | --- | --- |
| chromium | PASS (`folio-verified-chromium`) | PASS (`repair-release-chromium`) |
| firefox | PASS (`folio-release-firefox`) | PASS (`repair-release-firefox`) |
| webkit | PASS (`folio-recheck-webkit`) | PASS (`repair-verified-webkit`) |
| msedge | PASS (`folio-release-msedge`) | PASS (`repair-release-msedge`) |

The Electron host emitted native mutex/N-API/proposed-API warnings in its logs;
the extension's assertions passed. Chromium testing build 151.0.7922.34,
Firefox 153 build 1538 and WebKit 26.5 build 2336 were provisioned. Installed Edge
uses its available local channel. Safari/macOS was not run.

Failed intermediate WebKit and cache runs remain in the command ledger. No
waiting timeout, security assertion, exact scroll tolerance or accessibility
rule was relaxed to make a run pass. Live-provider quality is NOT_RUN and optional.

One intermediate WebKit run (`folio-verified-webkit`) completed functional and
accessibility checks but emitted one unhandled fetch error. The final full run
against the separate clean-runtime environment passed with zero browser errors;
the transient error's underlying cause is not established. An earlier Chromium
attempt overlapped replacement of its installed test artifact and timed out at
a workspace link; the independent final-wheel Chromium run passed. Neither
failed attempt is erased or called a successful gate.

The final repeated cross-client journey uses run
`f992db7ce3b548908c87cb52d662ce78` in `cross-client-release/pending.json` and
`completed.json`: waiting_for_approval→succeeded, reviewer approved, zero browser
errors and no manual token entry. This was actual VS Code Electron.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `product-acceptance` | 2026-11-17T19:46:11 | 2026-11-17T19:48:25 | 0 | 134.016 |
| `rc-acceptance` | 2026-11-17T19:48:26 | 2026-11-17T19:50:07 | 0 | 101.75 |
| `beta-acceptance` | 2026-11-17T19:50:08 | 2026-11-17T19:51:36 | 0 | 88.454 |
| `cross-client-electron-proof` | 2026-11-17T20:20:25 | 2026-11-17T20:22:50 | 0 | 144.781 |
| `cross-client-electron-release` | 2026-11-17T21:13:08 | 2026-11-17T21:15:01 | 0 | 112.938 |
| `connected-repair-installed` | 2026-11-17T19:52:46 | 2026-11-17T19:53:10 | 0 | 24.265 |
| `connected-repair-release` | 2026-11-17T21:02:29 | 2026-11-17T21:02:52 | 0 | 23.61 |
| `installed-process-restart` | 2026-11-17T20:49:49 | 2026-11-17T20:51:37 | 0 | 107.672 |
| `folio-verified-chromium` | 2026-11-17T21:14:56 | 2026-11-17T21:16:19 | 0 | 83.782 |
| `repair-release-chromium` | 2026-11-17T21:06:42 | 2026-11-17T21:07:02 | 0 | 20.515 |
| `folio-release-firefox` | 2026-11-17T21:07:02 | 2026-11-17T21:08:42 | 0 | 100.141 |
| `repair-release-firefox` | 2026-11-17T21:08:43 | 2026-11-17T21:09:15 | 0 | 32.25 |
| `folio-recheck-webkit` | 2026-11-17T21:12:11 | 2026-11-17T21:14:22 | 0 | 131.438 |
| `repair-verified-webkit` | 2026-11-17T21:14:56 | 2026-11-17T21:15:41 | 0 | 45.672 |
| `folio-release-msedge` | 2026-11-17T21:09:15 | 2026-11-17T21:10:36 | 0 | 80.86 |
| `repair-release-msedge` | 2026-11-17T21:10:36 | 2026-11-17T21:10:55 | 0 | 18.875 |
