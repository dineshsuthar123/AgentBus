# Connected customer repair acceptance

Status: local application-authoritative integration acceptance PASS (2026-10-27).
External provider/deployment validation remains BLOCKED. This is not production approval.
Baseline source: `3a36800`; implementation branch `feature/production-customer-repair`.
No external application endpoint, Stripe restricted key, customer mutation
authorization or staging deployment was supplied. No deployment or paid call occurs.

## Reproduce

```powershell
.venv\Scripts\python.exe -m pip install -e '.[repair,dev]'
$env:SYNDRA_TEST_POSTGRES = '1'
.venv\Scripts\python.exe -m pytest tests/test_connected_repair_postgres.py -vv
.venv\Scripts\python.exe -m pytest tests/test_connected_repair_contracts.py tests/test_connected_repair_network.py tests/test_connected_repair_api.py -q
.venv\Scripts\python.exe -m agentbus.repair.integration.contract --check
.venv\Scripts\python.exe -m syndra.cli control-schema export --check
```

PostgreSQL `initdb` and `pg_ctl` must be on PATH. Tests create a new owned cluster,
bind only numeric loopback, use synthetic identities and stop that cluster in
`finally`. They do not connect to an installed user's database, reset a repository,
or remove evidence directories. CI without PostgreSQL runs unit/API regression
tests; the explicit service suite is not replaced by those mocks.

Fresh artifact/browser flow (run from a checkout with installed Studio browser tools):

```powershell
cd studio
npm.cmd run build
cd ..
.venv\Scripts\python.exe scripts/prepare_studio.py
.venv\Scripts\python.exe -m build --no-isolation --outdir .tmp/production-repair-dist
python -m venv .tmp/production-repair-install
.tmp\production-repair-install\Scripts\python.exe -m pip install '.tmp/production-repair-dist/syndra-0.6.0b1-py3-none-any.whl[repair]'
.venv\Scripts\python.exe -m agentbus.repair.integration.acceptance --python .tmp/production-repair-install/Scripts/python.exe --output .tmp/production-repair-acceptance-UNIQUE
```

Choose a new output directory on each attempt; existing data is never removed to
make a test pass. Target/daemon subprocesses execute the installed wheel outside
the source directory. PostgreSQL, target application, normal authorization path,
local journal and Chrome exchange real network requests; Stripe is not involved.

## Results ledger

| Scenario | Result and evidence |
| --- | --- |
| Original demo | PASS: existing tests preserve 65/15/10/10; not silently relabelled. |
| New 100-record application | PASS: real 60/20/10/10, 60 target receipt-backed writes. |
| Five support fixes after preview | PASS: separate real run, 55 grants and five already-correct skips. |
| Refund/cancel/restriction changes | PASS: aggregate-lock race plus post-approval refund/cancel/suspension/deletion/manual/product/pending-change tests. |
| Duplicate/overlapping execution | PASS: one semantic effect across independent plans and delayed duplicate request. |
| Timeout after target commit | PASS: same receipt recovered after controller restart; no second effect. |
| Delayed request after stop | PASS: target revocation/tombstone rejects it. |
| Failure inside target transaction | PASS: no grant, receipt or new outbox row survives; local outcome remains unknown until reconciled. |
| Serialization failure | PASS: full transaction restarted once; one committed effect. |
| Changed approval/scope | PASS: re-sealed changed payloads, wrong principal/workspace, changed selection digest and rotated connection denied. |
| Missing evidence/business edges | PASS: deterministic policy tests cover payment/refund/dispute/cancellation, periods, mapping/history, restrictions and stale observations. |
| Access projection stuck | PASS: committed and verification pending remain separate; outbox processing + independent check reaches passed. |
| Revoked credentials/read-only | PASS: no write plan, but scoped authoritative analysis remains available. |
| Network boundaries | PASS: pinned addresses, rebinding, metadata, credentials in URL, mapped IPv6 and insecure production transport tests. |
| Pause/continue/new scan | PASS: no dispatch while paused, continuation under the same approval, and target fencing after a new scan. |
| Replay/import | PASS: coding executor refuses the repair workflow; unconnected import remains non-mutating. |
| 10000 candidates | PASS: application-authoritative scan, no Stripe calls; Windows/Python 3.14.6/PostgreSQL 18.6: 17.387 seconds, 3809215-byte traced Python peak, network page 100, UI page 50, concurrency 1, zero writes. Not a throughput SLA or a Stripe throughput measurement. |
| Official Stripe contracts | PASS: SDK 15.6.1/API 2026-08-26.dahlia fixtures, Connect/invoice/subscription/complete entitlements, account-shared Retry-After. |
| Official Stripe sandbox | BLOCKED: no authorized restricted provider key/account supplied. |
| Controller crash boundaries | PASS: real subprocess exits before execute/dispatch, after target commit and during verification; actual process termination while the target holds its transaction. Same durable operation recovered, no second effect. Not a certification of every hardware/power failure. |
| External production installation | BLOCKED: application-owner hook review, HTTPS endpoint, scoped credentials and concrete staging/live authorization required. |
| External payment race coordination | UNSUPPORTED by the reference: Stripe-authoritative plans remain analysis-only, not silently downgraded to application authority. |

The first full run exposed a packaging-extras assertion; the next exposed a real
concurrent terminal-task transition race. The adapter now recognizes an already
terminal task while another reconciler is completing the run update. A four-worker
barrier test holds that exact interleaving; the core immutable StateStore transition
rules were not relaxed. The final artifact audit also caught the old extra list
in the package auditor. That list and defensive fixtures now include `repair`,
with negative tests for missing and unexpected extras.
The sdist explicitly includes the subprocess crash fixture used by its shipped
PostgreSQL tests; broad runtime-directory inclusion was not added to fix it.

## Final verification ledger

| Check | Actual result |
| --- | --- |
| Full Python regression, real PostgreSQL enabled | PASS: 2150 passed, 11 skipped, 2 warnings, 1585.27 seconds. |
| Connected cases inside that full run | PASS: 34 contracts, 12 real-network error cases, 9 control API cases, 39 PostgreSQL cases. No service-test skips. |
| Post-audit package/security regression | PASS: 17 passed in 8.00 seconds, including two newly added missing/unexpected-extra cases. |
| Studio | PASS: 203 tests / 48 files, 189.37 seconds; lint and production TypeScript/Vite build pass. |
| Extension | PASS: 104 unit tests; compile, lint and generated-protocol freshness pass. Electron and remote CI were not rerun locally for this milestone. |
| Offline evaluation | PASS: core-offline / durable-parallel-fake, 9/9, score 100, 0 safety failures, 90.342 seconds, run `0e5365c5a40045f2802a931d0b065a85`. 36 fake requests / 432 fake tokens / 1 retry, no paid calls. |
| Contracts | PASS: application OpenAPI and control schema freshness; installed-wheel application contract also passes in isolated Python mode. |
| Compilation / patch | PASS: compileall for agentbus and syndra; git diff --check. |
| Distribution audit | PASS: actual wheel (434 entries) and sdist (780 entries), no runtime artifacts or metadata violations; the source-only crash helper is present. |
| Source/artifact security audit | PASS: 1012 source inputs including new untracked feature files, plus both archives, zero findings. |
| Fresh installed Studio | PASS: separate application/daemon/PostgreSQL, exact approval, dropped response, reload/reconcile, normal authorization, five browser states. |

The full run collected before the last two package-auditor regression cases were
added. Its follow-up 17-case run verifies the final auditor/fixture/manifest changes; these
overlapping counts must not be summed as one full-suite count. The 11 full-run
skips are existing POSIX-only or optional Windows symlink-privilege cases. Warnings
are the installed Starlette TestClient deprecation and an intentional legacy-env
precedence test. The 121-row Studio navigation test retains every assertion with
an explicit 15-second integration-test timeout instead of the five-second default;
it is not a rendering performance SLA.

Full Python execution used `agentbus.product.release_check._offline_environment()`
to strip provider credentials/select deterministic behavior and enabled
`SYNDRA_TEST_POSTGRES=1`. Underlying verification commands:

```powershell
.venv\Scripts\python.exe -m pytest -q --basetemp=C:/tmp/syndra-repair-final-head-c42a0790008c483abc1155dfa32483eb --junitxml=.tmp/production-repair-final-head-pytest.xml -o faulthandler_timeout=180
.venv\Scripts\python.exe -m pytest tests/test_release_packaging.py tests/test_release_packaging_audit.py tests/test_defensive_security_scorecard.py -q --junitxml=.tmp/production-repair-post-audit-pytest.xml
.venv\Scripts\python.exe -m compileall -q agentbus syndra
.venv\Scripts\python.exe -m agentbus.repair.integration.contract --check
.venv\Scripts\python.exe -m syndra.cli control-schema export --check
.venv\Scripts\python.exe -m agentbus.eval run --suite core-offline --variant durable-parallel-fake
npm.cmd --prefix studio test
npm.cmd --prefix studio run lint
npm.cmd --prefix studio run build
npm.cmd --prefix extensions/vscode test
npm.cmd --prefix extensions/vscode run compile
npm.cmd --prefix extensions/vscode run lint
npm.cmd --prefix extensions/vscode run protocol:check
.venv\Scripts\python.exe -m agentbus.release_packaging .tmp/production-repair-final-head-dist/syndra-0.6.0b1-py3-none-any.whl .tmp/production-repair-final-head-dist/syndra-0.6.0b1.tar.gz --json
.tmp\production-repair-install\Scripts\python.exe -I -m agentbus.repair.integration.contract --check
.venv\Scripts\python.exe -m agentbus.repair.integration.acceptance --python .tmp/production-repair-install/Scripts/python.exe --output .tmp/production-repair-final-head-acceptance
git diff --check
```

Use a new basetemp/output directory for reproduction, not the evidence paths above.
The security scan calls `audit_release_security` with the union of tracked files
and scoped new source files plus the two archives; nothing needs staging to include
new code. JSON audit evidence is in `.tmp/production-repair-final-head-security.json`
and `.tmp/production-repair-final-head-package-audit.json`.

A fresh environment installed the built wheel and its `repair` extra. The installed
target and daemon ran separately outside the source directory, against PostgreSQL
18.6. Chrome performed connection refresh, exact one-record approval, a response
dropped after execute, reload, reconciliation and normal authorization. This is
local integration evidence, not Stripe sandbox or external production validation.

The final installed-wheel run in `.tmp/production-repair-final-head-acceptance`
checks five states: exact desktop plan, recovered verification, mobile light,
mobile dark and desktop dark. All have zero axe WCAG A/AA violations, no horizontal
overflow, zero external requests and zero browser errors. There was exactly one
execute request; the independent normal access path reported allowed/propagated
at revision 2. The workspace remained Git-clean. This is automated accessibility
coverage plus visual inspection, not a claim of exhaustive assistive-device testing.

Final local evidence lives under `.tmp/production-repair-*`; raw databases, logs,
credentials and screenshots are not committed.

## Remaining boundaries

Application owners must implement/review all aggregate writer hooks, normal-access
verification and payment mapping, configure an approved HTTPS endpoint and scoped
credential references locally, and authorize a staging canary. Stripe sandbox
validation additionally needs a restricted test key, exact platform/optional
Connect account and matching test-mode evidence. No keys belong in chat or Studio.
The reference cannot coordinate independent Stripe dashboard/API refunds, so it
does not allow Stripe-authoritative writes. Compensation is deliberately unsupported.
The configured PostgreSQL 16 CI job has not run remotely; local evidence is Windows
11 / Python 3.14.6 / PostgreSQL 18.6 / Node 22.17.0 / npm 10.9.2.

Changes remain local on `feature/production-customer-repair`; no feature commit,
push, release, deployment or real customer mutation was performed. Unrelated local
artifacts were preserved.
