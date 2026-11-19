# Architecture and release scope

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

Decision: **NOT READY — BLOCKED**. The target remains a supervised, single-user
local pilot. Local deterministic success does not establish model quality,
production customer integration readiness or hosted isolation.

`syndra` is the product facade over `agentbus`. CLI, Studio and the VS Code
extension use the same control plane and durable SQLite records. The background
supervisor admits work and owns workspace/run operations. The execution engine
and task graph enforce transitions, attempt budgets, cancellation and leases.
The orchestrator routes planner/coder/verifier/reviewer operations. Deterministic
policy, exact revision-bound approval and managed dispatch remain authoritative;
model text, repository content and MCP results cannot grant capabilities.

Managed filesystem/process/Git/MCP tools are separate boundaries. Worktrees
isolate supported parallel edits. Independent verification/review gates publication.
Trace blobs, seals, provenance and replay make evidence inspectable. Repository
intelligence is advisory static analysis. Ollama/Azure adapters have offline
contract coverage; live quality, credentials and spending were outside authorization.

The React/TypeScript Studio ships in the Python distribution with content hashes
and font licenses. Its launch URL exchanges a single-use 30-second ticket for
an HttpOnly SameSite session. The extension uses the same backend and SecretStorage.
The synthetic repair system changes an owned SQLite reference application.
The pre-existing connected repair implementation uses HTTP and conditional
PostgreSQL corrections; only the disposable reference integration was exercised.
Stripe live evidence and real customer backends remain experimental/unverified.

## Preserved work and changes

Original branch `feature/production-customer-repair` had 24 tracked changes and
substantial untracked connected-repair work. `pre-existing.patch` and
`pre-existing-status.txt` preserve that baseline locally. No reset, clean, push,
merge or commit was performed. Inaccessible legacy temporary folders and user
demo repositories were left untouched. New source and tests must be intentionally
included when the founders prepare a candidate commit; Git-tracked files alone
are not the entire validated implementation.

The ownership, admission, interruption, configuration, import and UI defects
corrected here are listed in [the defect register](defect-register.md). The
historical Windows failure is independently reproduced through injected finalizer
failure; missing historical stderr prevents proving that this was its sole cause.

## Product support categories

| Category | Current scope |
| --- | --- |
| Available locally, validated offline | Deterministic CLI/runtime; exact permissions; durable approval; bounded parallelism; index/search; trace/replay; Studio; VS Code; synthetic repair |
| Experimental | Connected PostgreSQL reference repair; Ollama/Azure live use; unmeasured repositories and hardware; full assistive-technology coverage |
| Planned | Hosted operational readiness and independently verified tenant isolation |
| Unsupported | Unattended public execution, multi-tenant hosting, arbitrary untrusted OS processes treated as sandboxed, production payment mutations |

This audit is not a formal penetration test. Windows results do not establish
native Linux/macOS behavior. Final CI, dependency-security and extended-soak gates remain blockers.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `control-baseline` | 2026-11-17T19:26:15 | 2026-11-17T19:28:30 | 0 | 134.539 |
| `control-baseline-312` | 2026-11-17T19:29:32 | 2026-11-17T19:31:48 | 0 | 135.984 |
| `control-fixed-312-1` | 2026-11-17T19:32:27 | 2026-11-17T19:34:39 | 0 | 131.485 |
| `control-fixed-312-2` | 2026-11-17T20:01:37 | 2026-11-17T20:03:53 | 0 | 135.157 |
| `process-recovery-regression` | 2026-11-17T20:46:16 | 2026-11-17T20:48:49 | 0 | 152.078 |
