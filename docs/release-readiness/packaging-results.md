# Artifacts, installation and reproducibility

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

Final artifacts are local review artifacts, not published releases. Python
version 0.6.0b1; VSIX0.6.0-beta.1. Studio is compiled/staged with 58 asset/license
entries and SHA-256 manifest verification. Distribution audit checks metadata,
entry points, bounded contents, fixture inclusion, forbidden state/secrets and
wheel/sdist agreement. Release-specific audit reports are excluded from the
sdist; product documentation remains included. This avoids embedding an artifact's
own changing hash report within that same artifact.

| Local artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `.tmp/release-readiness/20261118/release-artifacts/syndra-0.6.0b1-py3-none-any.whl` | 1666986 | `1e62c61d6b08eb87265342f82a6abb9ec8e0a35fa98979da97a4cd1137450889` |
| `.tmp/release-readiness/20261118/release-artifacts/syndra-0.6.0b1.tar.gz` | 2103054 | `4714a595178b2cbaff0ba3ca9ec98fdbfd22d016ba72c03f89568f7485d276ff` |
| `.tmp/release-readiness/20261118/release-artifacts/syndra-vscode-0.6.0-beta.1.vsix` | 99889 | `4553f928ba32e46e949c963a56d8ab5c4872becb800ee2218fafa394e2623833` |

Two serial builds from stable source are compared semantically by package member
content and metadata. Archive timestamps can differ; a semantic match is not
claimed as byte-for-byte reproducibility. The earlier aggregate package audit
found two differences during active source/build changes. A separate concurrent
build hit a Windows file lock; both failures are preserved. Final audit below
is authoritative for these artifacts, not a rewritten historical result.

A direct `build --wheel` repeat reused dirty `build/lib` and included old Studio
chunks and fixture bytecode; semantic comparison correctly failed. The release
procedure is `python -m build --no-isolation`, which builds the wheel from a fresh
sdist extraction. A separate full repeat of that procedure matched semantically
(`package-audit-isolated`). Exact distribution hygiene passed for the selected
434-entry wheel/871 archive-entry sdist, including58 Studio assets/licenses.
Do not reuse the polluted direct-wheel output as a release artifact.
`artifact-source-equivalence` independently matched395 current wheel source/asset
files,783 sdist source files and 45 compiled VSIX files to the checkout/build output.

Fresh environments installed wheels through pip with network-resolved dependencies;
isolated `-I` import origin was under the environment prefix. Actual CLI quickstart,
Studio, synthetic/connected repair and installed-daemon process recovery were
exercised. Product/RC acceptance also checks uninstall and owned cleanup. No source
path import was allowed to substitute for the installed product smoke.

The full default release-security scanner originally failed because no selected
wheel/sdist existed. The final invocation explicitly includes wheel,sdist,VSIX
and both tracked and scoped new source paths, with defensive validation enabled.
That scan does not replace dependency advisory scans. Release approval is still
blocked by unresolved Node high findings and absent exact-commit CI.

| Gate / evidence prefix | Start UTC | End UTC | Exit | Seconds |
| --- | --- | --- | ---: | ---: |
| `controls-candidate-build` | 2026-11-17T21:05:38 | 2026-11-17T21:06:04 | 0 | 25.703 |
| `documentation-sdist-build` | 2026-11-17T21:22:43 | 2026-11-17T21:22:54 | 0 | 10.422 |
| `documentation-sdist-repeat` | 2026-11-17T21:22:54 | 2026-11-17T21:23:03 | 0 | 9.532 |
| `final-wheel-repeat` | 2026-11-17T21:19:44 | 2026-11-17T21:19:55 | 0 | 11.094 |
| `isolated-repeat-build` | 2026-11-17T21:27:01 | 2026-11-17T21:27:26 | 0 | 24.812 |
| `distribution-verify-resumed` | 2026-11-18T08:22:00 | 2026-11-18T08:22:00 | 0 | 0.36 |
| `package-audit-complete` | 2026-11-17T21:23:04 | 2026-11-17T21:23:07 | 1 | 2.516 |
| `package-audit-resumed` | 2026-11-18T08:22:00 | 2026-11-18T08:22:03 | 0 | 2.438 |
| `artifact-source-equivalence-resumed` | 2026-11-18T08:22:03 | 2026-11-18T08:22:04 | 0 | 1.437 |
| `vsix-release` | 2026-11-17T21:03:12 | 2026-11-17T21:03:21 | 0 | 8.844 |
| `vsix-audit-release` | 2026-11-17T21:07:59 | 2026-11-17T21:08:00 | 0 | 0.657 |
| `clean-runtime-install` | 2026-11-17T21:03:50 | 2026-11-17T21:04:37 | 0 | 46.516 |
| `clean-runtime-origin` | 2026-11-17T21:04:37 | 2026-11-17T21:04:38 | 0 | 1.156 |
| `clean-runtime-final-wheel` | 2026-11-17T21:07:28 | 2026-11-17T21:07:35 | 0 | 7.0 |
| `clean-runtime-quickstart-final` | 2026-11-17T21:16:35 | 2026-11-17T21:16:39 | 0 | 3.578 |
| `clean-runtime-audit` | 2026-11-17T21:16:39 | 2026-11-17T21:16:46 | 0 | 7.047 |
| `security-resumed` | 2026-11-18T08:22:05 | 2026-11-18T08:22:11 | 0 | 6.734 |
