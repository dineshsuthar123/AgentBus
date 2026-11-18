# Security review and trust boundaries

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

Security release gate: **FAIL** because known high dependency findings remain.
The narrower runtime-only Node audits and Python audit pass; those do not waive
the source/build toolchain gate.

| Boundary | Exercised controls and evidence | Practical limit |
| --- | --- | --- |
| Prompt/repository/MCP data → authority | Deterministic policy, declared capabilities, descriptor binding, exact revision/hash approvals; hostile peer/path and stale approval tests | Offline adversarial cases, not all attacks or provider quality |
| Filesystem/Git | Canonical containment; traversal/device/UNC/link/race checks; workspace identity; hostile Git forms; worktree fencing | Windows privilege-dependent link cases disclosed; no OS tenant isolation |
| Process | Executable alias catalog, environment filtering, time/output/child bounds, cancellation/Job Objects, side-effect canary | Managed processes are not a general OS sandbox; zero-child budget may refuse venv startup |
| MCP | Allowlist, schema and protocol bounds; stdio/HTTP failures, timeout/cancellation, peer cleanup, exact approval | Real local fake peer; no arbitrary remote server security claim |
| Local API | Numeric loopback, bearer CLI, exact Origin and CSRF for browser writes, 1MB request bound, no broad CORS; single-use bootstrap, HttpOnly SameSite sessions | Shared OS account is trusted; not public/multi-tenant safe |
| Availability | 100 req/s token rate, burst256, 64 REST/32 SSE slots; background admission; safe 429/503 and Retry-After | Single-user defaults, no capacity SLA; saturated SSE may receive 429 before503 |
| Durable recovery | Identity-specific owner release, transactional transitions, leases, exact continuations; drift fails before tools | Unknown effects after interruption require review; no promise of safe arbitrary automatic replay |
| Trace/artifact/support | Integrity/tamper tests, bounded archives/imports, redaction, support-bundle privacy, artifact audits | Retained local artifacts still require owner disk access controls |
| Customer repair | Tenant/source/revision checks, preview and exact approval, idempotency/reconciliation; actual SQLite/Postgres oracle | Disposable reference systems only; no live payment or customer authority proved |

Two controlled policy mutations (accept rejected approval; disable expiry check)
were killed by real tests in a disposable source copy. Restored tests passed and
the real source hash was unchanged (`mutation-results.json`). This is targeted
mutation evidence, not a whole-project mutation score.

Full Bandit scan reported 8 medium and 0 high findings: seven B608 SQL construction
sites and one B314 Java XML parser site. Manual review found bound values and
constant/validated SQL structure; XML rejects DTD/entities and bounds input.
Raw medium findings remain in `bandit-results.json`; they were not suppressed.
The separate high-severity/medium-confidence Bandit gate passed. No claim of zero
raw static findings is made.

`pip-audit` originally found advisories in validation pip 25.0.1. Updating that
disposable environment to pip 26.3 cleared the Python audit. Compatible Node
updates reduced Studio from 19 findings to12 high and extension from24 to16
(14 high,2 moderate). Production-only Node audits report0. High findings cascade
from picomatch into globbing/build/lint/test tools. The extension also reports
moderate glob/minimatch advisories. These are scanner dependency counts, not26
distinct high vulnerabilities. Do not use `npm audit fix --force` to hide them.

The security mapping follows [OWASP Agentic Top 10 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
for goal/tool/identity/memory/inter-agent/availability boundaries and
[MCP security guidance](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices).
The [OWASP LLM project page](https://genai.owasp.org/llm-top-10/) was checked;
announcement timing is not treated as proof of new published category names.
[picomatch advisory](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) is the
remaining high root issue. This is a scoped defensive review, not certification.

The final source/artifact security audit scanned 1,032 files and all three selected
artifacts, with 0 findings and 23 controlled defensive boundaries exercised. The
clean-runtime dependency audit also passed after updating pip. The unpublished
Syndra version is absent from PyPI and is skipped by that advisory registry;
first-party source/artifact checks are separate evidence. Host OS, Python/Node
distributions and VS Code binaries are identified by version; this is not a
complete vendor-binary vulnerability certification.

The Windows MCP observer defect was confined to its test: the corrected read-only
query and live-peer regression replace `os.kill(pid,0)`, whose Windows behavior is
documented by [Python](https://docs.python.org/3.12/library/os.html#os.kill).
The full-suite failure and focused 29-test pass are both retained.

PostgreSQL review used the local Supabase Postgres guidance for bound queries,
indexes, short transactions and least privilege. The reference backend sets5s
statement/3s lock timeouts, uses tenant/purchase keys, conditional revisions,
committed-effect uniqueness and immutable receipt triggers. Actual race/rollback
and pagination tests ran. Startup also performs schema migration: a dedicated
least-privilege runtime role, migration-role separation and hosted RLS isolation
were not qualified. No query-plan or connection-pool capacity claim is made.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `pip-audit-final` | 2026-11-17T20:43:32 | 2026-11-17T20:43:40 | 0 | 8.203 |
| `clean-runtime-audit` | 2026-11-17T21:16:39 | 2026-11-17T21:16:46 | 0 | 7.047 |
| `python-audit-resumed` | 2026-11-18T08:17:02 | 2026-11-18T08:17:06 | 0 | 3.813 |
| `bandit-high-gate` | 2026-11-17T20:43:40 | 2026-11-17T20:43:50 | 0 | 9.5 |
| `studio-audit-final` | 2026-11-17T20:43:13 | 2026-11-17T20:43:21 | 1 | 8.531 |
| `studio-audit-resumed` | 2026-11-18T08:17:02 | 2026-11-18T08:17:10 | 1 | 8.266 |
| `vscode-audit-final` | 2026-11-17T20:43:21 | 2026-11-17T20:43:27 | 1 | 5.187 |
| `vscode-audit-resumed` | 2026-11-18T08:17:02 | 2026-11-18T08:17:11 | 1 | 8.594 |
| `studio-runtime-audit` | 2026-11-17T20:43:27 | 2026-11-17T20:43:29 | 0 | 2.5 |
| `vscode-runtime-audit` | 2026-11-17T20:43:30 | 2026-11-17T20:43:32 | 0 | 2.078 |
| `mutation-probe-fixed` | 2026-11-17T19:58:39 | 2026-11-17T19:58:49 | 0 | 9.532 |
| `release-security-complete` | 2026-11-17T21:27:12 | 2026-11-17T21:27:19 | 0 | 7.391 |
| `mcp-observer-regression` | 2026-11-17T21:12:04 | 2026-11-17T21:12:10 | 0 | 5.891 |
