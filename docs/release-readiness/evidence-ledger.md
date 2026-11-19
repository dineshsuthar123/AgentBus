# Executed command evidence

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

## Command records

The command is executed from the recorded relative working directory. `PYTHON` in helper invocations resolves to the named environment interpreter. Metadata `python` describes the driver; explicit child interpreters are identified in the command and origin logs. A successful wrapper is not substituted for a failing child result.

### control-baseline

- Start/end: 2026-11-17T19:26:15.710990+00:00 → 2026-11-17T19:28:30.250070+00:00.
- Exit 0; 134.539 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.venv\\Scripts\\python.exe", "-m", "syndra.control.acceptance"]`.
- Evidence: `control-baseline.json`, `control-baseline.log`; log SHA-256 `56546256bb2a9bc6940e474d8272ca586fe590e5be7c7452b795e88401155de4`.

### install-validation-deps

- Start/end: 2026-11-17T19:27:17.206823+00:00 → 2026-11-17T19:28:49.318749+00:00.
- Exit 0; 92.109 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pip", "install", "-e", ".[dev,ide,mcp,repair]", "pytest-cov", "pip-audit", "bandit"]`.
- Evidence: `install-validation-deps.json`, `install-validation-deps.log`; log SHA-256 `2b1cb606be56b1ad5b5aac8a940b77e1eb6d7e01ccc44da619014ea712513d3c`.

### pytest-baseline

- Start/end: 2026-11-17T19:29:10.278111+00:00 → 2026-11-17T19:46:32.325119+00:00.
- Exit 1; 1042.047 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.venv\\Scripts\\python.exe", "-m", "pytest", "-q", "--basetemp=.tmp/release-readiness/20261118/pytest-baseline-tmp", "--junitxml=.tmp/release-readiness/20261118/pytest-baseline.xml", "-o", "faulthandler_timeout=180"]`.
- Evidence: `pytest-baseline.json`, `pytest-baseline.log`; log SHA-256 `de74576be79e3c1de300ab98c8ec07f9286b818850e8ee27ea89441b99d5c8c2`.

### control-baseline-312

- Start/end: 2026-11-17T19:29:32.585815+00:00 → 2026-11-17T19:31:48.560752+00:00.
- Exit 0; 135.984 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.control.acceptance"]`.
- Evidence: `control-baseline-312.json`, `control-baseline-312.log`; log SHA-256 `56546256bb2a9bc6940e474d8272ca586fe590e5be7c7452b795e88401155de4`.

### owner-regression-red

- Start/end: 2026-11-17T19:30:22.646216+00:00 → 2026-11-17T19:30:25.739888+00:00.
- Exit 1; 3.093 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_supervisor.py", "-k", "completion or concurrent_resume", "-q", "--basetemp=.tmp/release-readiness/20261118/owner-red", "--junitxml=.tmp/release-readiness/20261118/owner-red.xml"]`.
- Evidence: `owner-regression-red.json`, `owner-regression-red.log`; log SHA-256 `82af2afd6fdac4877d20da68a4d11bce0392c460d3e91af625bb0558b742e9bf`.

### owner-regression-green

- Start/end: 2026-11-17T19:30:52.281286+00:00 → 2026-11-17T19:30:55.923676+00:00.
- Exit 0; 3.641 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_supervisor.py", "tests/test_mcp_stdio_transport.py", "-q", "--basetemp=.tmp/release-readiness/20261118/owner-green", "--junitxml=.tmp/release-readiness/20261118/owner-green.xml"]`.
- Evidence: `owner-regression-green.json`, `owner-regression-green.log`; log SHA-256 `824af4ab0dd2bb34b086024ae75d46064d2bb0044a5d0eda5640903c91fcf6b9`.

### studio-npm-ci

- Start/end: 2026-11-17T19:30:56.321487+00:00 → 2026-11-17T19:32:16.036120+00:00.
- Exit 0; 79.719 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "ci"]`.
- Evidence: `studio-npm-ci.json`, `studio-npm-ci.log`; log SHA-256 `97d3f9d17b2a143f09817f20511384e00180cde4f96f7f1dde2eb8919e3bef4e`.

### vscode-npm-ci

- Start/end: 2026-11-17T19:31:24.653606+00:00 → 2026-11-17T19:32:12.844270+00:00.
- Exit 0; 48.188 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "ci"]`.
- Evidence: `vscode-npm-ci.json`, `vscode-npm-ci.log`; log SHA-256 `10adc51b5f4040b4e1c52df12ff15e16d831aab19c6977f8fe11af0666a0e4aa`.

### pip-audit

- Start/end: 2026-11-17T19:31:34.908452+00:00 → 2026-11-17T19:31:36.543097+00:00.
- Exit 2; 1.641 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pip_audit", "--disable-pip", "--format", "json"]`.
- Evidence: `pip-audit.json`, `pip-audit.log`; log SHA-256 `52c4897450b1b56b324b7c0d28194081d9dd7426b682aef3422d4c2579eb2a18`.

### pip-audit-local

- Start/end: 2026-11-17T19:32:00.510456+00:00 → 2026-11-17T19:32:07.815063+00:00.
- Exit 1; 7.296 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pip_audit", "--local", "--skip-editable", "--format", "json", "--output", ".tmp/release-readiness/20261118/pip-audit-results.json"]`.
- Evidence: `pip-audit-local.json`, `pip-audit-local.log`; log SHA-256 `9205ea0c3cc5fce1219c831a5a7a8ec8877272d40e79f6044d75494a48662bbe`.

### bandit

- Start/end: 2026-11-17T19:32:08.246680+00:00 → 2026-11-17T19:32:16.801641+00:00.
- Exit 1; 8.547 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "bandit", "-r", "agentbus", "syndra", "-f", "json", "-o", ".tmp/release-readiness/20261118/bandit-results.json", "--severity-level", "medium", "--confidence-level", "medium"]`.
- Evidence: `bandit.json`, `bandit.log`; log SHA-256 `c8234ef49555a669d09c39da3b8b57c1f113b8e6464351c174a0386b1f1e61f7`.

### control-fixed-312-1

- Start/end: 2026-11-17T19:32:27.683469+00:00 → 2026-11-17T19:34:39.166496+00:00.
- Exit 0; 131.485 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.control.acceptance"]`.
- Evidence: `control-fixed-312-1.json`, `control-fixed-312-1.log`; log SHA-256 `56546256bb2a9bc6940e474d8272ca586fe590e5be7c7452b795e88401155de4`.

### studio-audit-before

- Start/end: 2026-11-17T19:32:53.122143+00:00 → 2026-11-17T19:32:56.518335+00:00.
- Exit 1; 3.391 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "audit", "--json"]`.
- Evidence: `studio-audit-before.json`, `studio-audit-before.log`; log SHA-256 `d51249ab5d8f6f88697bb57d81a544ceb3c819b2543007794cea9a043dbdb3f8`.

### vscode-audit-before

- Start/end: 2026-11-17T19:32:53.122143+00:00 → 2026-11-17T19:32:56.372386+00:00.
- Exit 1; 3.25 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "audit", "--json"]`.
- Evidence: `vscode-audit-before.json`, `vscode-audit-before.log`; log SHA-256 `0d2c98ee2492321677adf624f080b245789cb05cf2d2c0543984dc4529ca8612`.

### studio-audit-fix

- Start/end: 2026-11-17T19:33:09.306973+00:00 → 2026-11-17T19:35:12.438262+00:00.
- Exit 1; 123.125 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "audit", "fix"]`.
- Evidence: `studio-audit-fix.json`, `studio-audit-fix.log`; log SHA-256 `2effdfcbd308b235b9e8821c9773be77c40218401766e189275bf8c6e9640be6`.

### vscode-audit-fix

- Start/end: 2026-11-17T19:33:19.502652+00:00 → 2026-11-17T19:35:04.548772+00:00.
- Exit 1; 105.047 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "audit", "fix"]`.
- Evidence: `vscode-audit-fix.json`, `vscode-audit-fix.log`; log SHA-256 `62259625e274fc3cb37266166f9b0a6816e57a72e243fdb0bd9665c0abb33f3b`.

### pip-upgrade-validation

- Start/end: 2026-11-17T19:33:29.693315+00:00 → 2026-11-17T19:33:41.071590+00:00.
- Exit 0; 11.375 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pip", "install", "--upgrade", "pip"]`.
- Evidence: `pip-upgrade-validation.json`, `pip-upgrade-validation.log`; log SHA-256 `bf4db65e1724da7229f9d1aebd6aaebc7e7afabbf06d6536894e93ac49f7a088`.

### release-security

- Start/end: 2026-11-17T19:34:41.306294+00:00 → 2026-11-17T19:34:51.456382+00:00.
- Exit 1; 10.156 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.release_security", "--json"]`.
- Evidence: `release-security.json`, `release-security.log`; log SHA-256 `cd6089ad99ba70c37d236abd37fd80d9a8dd25d889164b1bc59806f4d91fe0af`.

### core-eval

- Start/end: 2026-11-17T19:34:51.506731+00:00 → 2026-11-17T19:35:49.009139+00:00.
- Exit 0; 57.5 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.eval", "run", "--suite", "core-offline", "--variant", "durable-parallel-fake"]`.
- Evidence: `core-eval.json`, `core-eval.log`; log SHA-256 `aad3799b94d46605697a643a9a944da265555d4a482f589e14a05a5b30af0f22`.

### intelligence-eval

- Start/end: 2026-11-17T19:35:15.251279+00:00 → 2026-11-17T19:35:25.842137+00:00.
- Exit 1; 10.594 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.eval", "run", "--suite", "repository-intelligence", "--variant", "deterministic"]`.
- Evidence: `intelligence-eval.json`, `intelligence-eval.log`; log SHA-256 `3e7e0f99641dc7855fb2c3c4092ab2296241cd3a6086852798199f42c6169c2e`.

### studio-audit-after

- Start/end: 2026-11-17T19:37:08.153522+00:00 → 2026-11-17T19:37:11.400356+00:00.
- Exit 1; 3.235 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "audit", "--json"]`.
- Evidence: `studio-audit-after.json`, `studio-audit-after.log`; log SHA-256 `34abe53185cf1edff1e3c41e3167909ceebfeeaec48509b8e7be08ef018dc2ef`.

### branding-regression

- Start/end: 2026-11-17T19:37:29.661282+00:00 → 2026-11-17T19:37:32.917571+00:00.
- Exit 1; 3.266 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_branding.py", "-q", "--basetemp=.tmp/release-readiness/20261118/branding", "--junitxml=.tmp/release-readiness/20261118/branding.xml"]`.
- Evidence: `branding-regression.json`, `branding-regression.log`; log SHA-256 `e3b05b501e360f822ee08eebeb9c295cc77e8b4477e8b398e2629e0ddc8315a1`.

### studio-typecheck

- Start/end: 2026-11-17T19:38:54.941375+00:00 → 2026-11-17T19:39:01.662514+00:00.
- Exit 0; 6.719 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "typecheck"]`.
- Evidence: `studio-typecheck.json`, `studio-typecheck.log`; log SHA-256 `8ccc65c007ea6c05d06d79a6429afe94506cec93deef8284de65434984b1cb0b`.

### studio-lint

- Start/end: 2026-11-17T19:39:02.048350+00:00 → 2026-11-17T19:39:14.110176+00:00.
- Exit 0; 12.062 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "lint"]`.
- Evidence: `studio-lint.json`, `studio-lint.log`; log SHA-256 `bbf3a59c9d157ed5e71457fcbec7947d2fe8c01cda8ffb8e529e15bf4be67362`.

### vscode-unit

- Start/end: 2026-11-17T19:39:12.331174+00:00 → 2026-11-17T19:39:17.144211+00:00.
- Exit 0; 4.813 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "test"]`.
- Evidence: `vscode-unit.json`, `vscode-unit.log`; log SHA-256 `0b1a6a68a409210ff4d8814fe951c1f22e824c1f30f726426c0f1f976a71d7c0`.

### vscode-protocol

- Start/end: 2026-11-17T19:39:56.523436+00:00 → 2026-11-17T19:40:00.184035+00:00.
- Exit 0; 3.671 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "protocol:check"]`.
- Evidence: `vscode-protocol.json`, `vscode-protocol.log`; log SHA-256 `62b8320dd56dfff81fa0ee2fc9ed37a915b01af52b196c83054d01b7b58eedaf`.

### studio-tests

- Start/end: 2026-11-17T19:39:56.538610+00:00 → 2026-11-17T19:42:28.503035+00:00.
- Exit 0; 151.969 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "test"]`.
- Evidence: `studio-tests.json`, `studio-tests.log`; log SHA-256 `be8abbb9903769e44e66f7cbbc538d66c0a69e16272fd840bf10a18f60de6a23`.

### vscode-lint

- Start/end: 2026-11-17T19:40:00.379214+00:00 → 2026-11-17T19:40:05.975314+00:00.
- Exit 0; 5.594 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "lint"]`.
- Evidence: `vscode-lint.json`, `vscode-lint.log`; log SHA-256 `657ef0843b7ba26bbbdc9c9d3b99b90baaae5ccfcdd52726d2e369d5b697d99f`.

### vscode-integration

- Start/end: 2026-11-17T19:40:06.183762+00:00 → 2026-11-17T19:40:14.586881+00:00.
- Exit 1; 8.391 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "test:integration"]`.
- Evidence: `vscode-integration.json`, `vscode-integration.log`; log SHA-256 `f4d67da92ccfc62f0d072ff214f8d7d39ed0a9b96c71f236ad0c1d4ba785316a`.

### vscode-product

- Start/end: 2026-11-17T19:40:14.805579+00:00 → 2026-11-17T19:40:33.020028+00:00.
- Exit 1; 18.219 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "test:product"]`.
- Evidence: `vscode-product.json`, `vscode-product.log`; log SHA-256 `98027a78f2df99f7fc1a2557ae4f71121d2e4efc4969d1ce866a8fdd6a97664e`.

### vscode-package

- Start/end: 2026-11-17T19:40:33.242396+00:00 → 2026-11-17T19:40:39.186623+00:00.
- Exit 0; 5.953 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "package"]`.
- Evidence: `vscode-package.json`, `vscode-package.log`; log SHA-256 `a839d9f4425924dfcc12038601f291be4ce7160723b7a9d0ee3a9611d12bd78c`.

### vscode-package-audit

- Start/end: 2026-11-17T19:40:39.389483+00:00 → 2026-11-17T19:40:40.150777+00:00.
- Exit 0; 0.765 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "package:audit"]`.
- Evidence: `vscode-package-audit.json`, `vscode-package-audit.log`; log SHA-256 `259ddf70f71868527cf8d409ba0eb81ee9a36d2c6cf7835fb104d9f460c2e8d3`.

### mcp-owner-repair-regression

- Start/end: 2026-11-17T19:42:23.269925+00:00 → 2026-11-17T19:43:04.561862+00:00.
- Exit 1; 41.296 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_mcp_owner_recovery.py", "tests/test_customer_repair.py", "tests/test_repository_intelligence_evaluation.py", "tests/test_branding.py", "tests/test_ci_release_gates.py", "-q", "--basetemp=.tmp/release-readiness/20261118/mcp-repair-new", "--junitxml=.tmp/release-readiness/20261118/mcp-repair-new.xml"]`.
- Evidence: `mcp-owner-repair-regression.json`, `mcp-owner-repair-regression.log`; log SHA-256 `6d27510001a1b8b6986a0ead6ce6543ac7fbb6c68e7c3611038814c018844dc9`.

### studio-build

- Start/end: 2026-11-17T19:42:28.703475+00:00 → 2026-11-17T19:42:36.986395+00:00.
- Exit 0; 8.281 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "build"]`.
- Evidence: `studio-build.json`, `studio-build.log`; log SHA-256 `4b8499bb04391ab9dc48628c275eea8bee700fab95089bb8126dc8cc993ef885`.

### showcase-build

- Start/end: 2026-11-17T19:42:37.194396+00:00 → 2026-11-17T19:42:45.023483+00:00.
- Exit 0; 7.829 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "build:showcase"]`.
- Evidence: `showcase-build.json`, `showcase-build.log`; log SHA-256 `73714eed02027fac6f91cc25bc1856337f97f72e8a69754f587144d53c1e6c12`.

### showcase-smoke

- Start/end: 2026-11-17T19:42:45.229814+00:00 → 2026-11-17T19:42:46.379238+00:00.
- Exit 1; 1.141 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "test:showcase"]`.
- Evidence: `showcase-smoke.json`, `showcase-smoke.log`; log SHA-256 `1a5855eee7bf845fafaa0ed3130b47dc185ef6a6044f0a57a36990b6303eb1e0`.

### prepare-studio

- Start/end: 2026-11-17T19:42:46.592521+00:00 → 2026-11-17T19:42:47.776943+00:00.
- Exit 0; 1.187 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/prepare_studio.py"]`.
- Evidence: `prepare-studio.json`, `prepare-studio.log`; log SHA-256 `30967d6dd0fd2ae66d99c74eee88dda27504bb2278d4c39f612fd95e1195b1b5`.

### distribution-build

- Start/end: 2026-11-17T19:42:47.966258+00:00 → 2026-11-17T19:43:17.550987+00:00.
- Exit 0; 29.578 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/dist"]`.
- Evidence: `distribution-build.json`, `distribution-build.log`; log SHA-256 `a3d030689b04b5159165faade1bbeb9b2967b5504447f8ae6341a9f9e601b632`.

### vscode-integration-fresh

- Start/end: 2026-11-17T19:42:49.091186+00:00 → 2026-11-17T19:47:10.829525+00:00.
- Exit 0; 261.734 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "test:integration"]`.
- Evidence: `vscode-integration-fresh.json`, `vscode-integration-fresh.log`; log SHA-256 `e87fbf8db0196d265e9570f8033a9c8f48e04118348f0f16329d2e06e349fe4f`.

### intelligence-eval-fixed

- Start/end: 2026-11-17T19:42:59.170529+00:00 → 2026-11-17T19:43:08.879907+00:00.
- Exit 0; 9.703 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.eval", "run", "--suite", "repository-intelligence", "--variant", "deterministic"]`.
- Evidence: `intelligence-eval-fixed.json`, `intelligence-eval-fixed.log`; log SHA-256 `71f06f5d29672d5937a56e118cd95d85cc16d94f48a35e4732b1901a7d9bdb57`.

### pip-audit-clean

- Start/end: 2026-11-17T19:43:09.294072+00:00 → 2026-11-17T19:43:12.347459+00:00.
- Exit 0; 3.047 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pip_audit", "--local", "--skip-editable", "--format", "json", "--output", ".tmp/release-readiness/20261118/pip-audit-clean-results.json"]`.
- Evidence: `pip-audit-clean.json`, `pip-audit-clean.log`; log SHA-256 `4e4757ff226a44b5c448b1c4624619419c22d5272463dc19510b4754871a72f9`.

### distribution-verify

- Start/end: 2026-11-17T19:43:17.749368+00:00 → 2026-11-17T19:43:18.070801+00:00.
- Exit 0; 0.312 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/verify_distribution.py", ".tmp/release-readiness/20261118/dist"]`.
- Evidence: `distribution-verify.json`, `distribution-verify.log`; log SHA-256 `04cf35af18b544fa4fe7de3a776f461956e15c4fed45719b5eee4f2d9bcc580d`.

### studio-focused-diagnostic

- Start/end: 2026-11-17T19:43:29.231931+00:00 → 2026-11-17T19:43:32.339390+00:00.
- Exit 0; 3.11 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\node.exe", "node_modules/vitest/vitest.mjs", "run", "src/state/layoutPreferences.test.ts", "--reporter", "verbose", "--pool", "threads", "--maxWorkers", "1"]`.
- Evidence: `studio-focused-diagnostic.json`, `studio-focused-diagnostic.log`; log SHA-256 `2c3dbcc91b03aa49ae040c3f64a3bbafb613567c4176b1bd8cf41a0323c078bc`.

### playwright-install

- Start/end: 2026-11-17T19:44:15.259608+00:00 → 2026-11-17T19:48:53.773956+00:00.
- Exit 0; 278.516 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\node.exe", "node_modules/playwright/cli.js", "install", "chromium", "firefox", "webkit"]`.
- Evidence: `playwright-install.json`, `playwright-install.log`; log SHA-256 `21acd014fedbb60a40ea52337f4eb66cc1df80aa6804710763d2a53d951d8873`.

### mcp-repair-regression-final

- Start/end: 2026-11-17T19:44:25.466585+00:00 → 2026-11-17T19:44:47.728448+00:00.
- Exit 1; 22.25 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_mcp_owner_recovery.py", "tests/test_customer_repair.py", "-q", "--basetemp=.tmp/release-readiness/20261118/mcp-repair-final", "--junitxml=.tmp/release-readiness/20261118/mcp-repair-final.xml"]`.
- Evidence: `mcp-repair-regression-final.json`, `mcp-repair-regression-final.log`; log SHA-256 `fea655a0ae63ae7f74cd38eb81c38224a65d912cd794e1122b691eec5d78cea4`.

### fresh-wheel-install

- Start/end: 2026-11-17T19:45:13.352971+00:00 → 2026-11-17T19:46:22.298552+00:00.
- Exit 0; 68.953 seconds; timeout=False.
- cwd: `.`; command: `[".tmp\\release-readiness\\20261118\\installed312\\Scripts\\python.exe", "-m", "pip", "install", ".tmp/release-readiness/20261118/dist/syndra-0.6.0b1-py3-none-any.whl[ide,mcp,repair,dev]"]`.
- Evidence: `fresh-wheel-install.json`, `fresh-wheel-install.log`; log SHA-256 `b90d39f1d13292c5f42170c52d228951cccb135ceee1994d43fff78935044e36`.

### product-acceptance

- Start/end: 2026-11-17T19:46:11.951081+00:00 → 2026-11-17T19:48:25.967629+00:00.
- Exit 0; 134.016 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.product_acceptance", "--repeat", "2"]`.
- Evidence: `product-acceptance.json`, `product-acceptance.log`; log SHA-256 `a4dee545bf66979de8222732ce60ff7587c019cffd372a663a2a87b9c64867b3`.

### final-focused

- Start/end: 2026-11-17T19:46:22.177204+00:00 → 2026-11-17T19:46:56.239762+00:00.
- Exit 0; 34.062 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_mcp_owner_recovery.py", "tests/test_customer_repair.py", "tests/test_control_supervisor.py", "tests/test_ci_release_gates.py", "tests/test_branding.py", "-q", "--basetemp=.tmp/release-readiness/20261118/final-focused", "--junitxml=.tmp/release-readiness/20261118/final-focused.xml"]`.
- Evidence: `final-focused.json`, `final-focused.log`; log SHA-256 `7b5f7d8fba68f3a315f3c3d499633560ea3f63bc31f1be905dd2982642d80598`.

### admission-api

- Start/end: 2026-11-17T19:47:56.505629+00:00 → 2026-11-17T19:48:16.450596+00:00.
- Exit 0; 19.953 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_admission.py", "tests/test_control_app.py", "tests/test_control_supervisor.py", "-q", "--basetemp=.tmp/release-readiness/20261118/admission", "--junitxml=.tmp/release-readiness/20261118/admission.xml"]`.
- Evidence: `admission-api.json`, `admission-api.log`; log SHA-256 `3b192d5a8807f9d7835a18a59dfa038d06599195a623be79021c03320134973d`.

### rc-acceptance

- Start/end: 2026-11-17T19:48:26.165786+00:00 → 2026-11-17T19:50:07.905548+00:00.
- Exit 0; 101.75 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.rc_acceptance"]`.
- Evidence: `rc-acceptance.json`, `rc-acceptance.log`; log SHA-256 `8f3b573d89abeda607afb54d9342045667e736c1506150adb15c91a0d8b80956`.

### beta-acceptance

- Start/end: 2026-11-17T19:50:08.071479+00:00 → 2026-11-17T19:51:36.521981+00:00.
- Exit 0; 88.454 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.beta_acceptance"]`.
- Evidence: `beta-acceptance.json`, `beta-acceptance.log`; log SHA-256 `158ef258e30fdadf1671410bcbebd932ae7416d3598292764cc71809e7a23337`.

### pytest-final-312

- Start/end: 2026-11-17T19:50:16.624237+00:00 → 2026-11-17T20:13:54.835903+00:00.
- Exit 1; 1418.203 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "-q", "--basetemp=.tmp/release-readiness/20261118/pytest-final-312-tmp", "--junitxml=.tmp/release-readiness/20261118/pytest-final-312.xml", "--cov=agentbus", "--cov=syndra", "--cov-branch", "--cov-report=json:.tmp/release-readiness/20261118/coverage.json", "--cov-report=term", "-o", "faulthandler_timeout=180"]`.
- Evidence: `pytest-final-312.json`, `pytest-final-312.log`; log SHA-256 `e7f19bcdff7482b730f9f157819eec87da73af94eb2de94e49f623598072db1f`.

### vscode-product-fresh

- Start/end: 2026-11-17T19:50:26.851603+00:00 → 2026-11-17T19:52:32.819107+00:00.
- Exit 0; 125.968 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "test:product"]`.
- Evidence: `vscode-product-fresh.json`, `vscode-product-fresh.log`; log SHA-256 `f08a7dd28a5d00d11dfa36a4a985d58444262135406791c8eaddbab1aae5e1a1`.

### distribution-build-final

- Start/end: 2026-11-17T19:50:37.018205+00:00 → 2026-11-17T19:51:03.985083+00:00.
- Exit 0; 26.968 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/dist-final"]`.
- Evidence: `distribution-build-final.json`, `distribution-build-final.log`; log SHA-256 `32782702605c2539f91e2d17bfad884e8816a08496bdf988a52a661c1fc3784d`.

### reliability

- Start/end: 2026-11-17T19:51:36.696104+00:00 → 2026-11-17T19:52:03.406933+00:00.
- Exit 0; 26.719 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.cli", "validate", "reliability", "--json"]`.
- Evidence: `reliability.json`, `reliability.log`; log SHA-256 `4564effaa75287441983230f2f7bdcbb73278c00e4cc98c8cb8da84c55902678`.

### fresh-wheel-reinstall-final

- Start/end: 2026-11-17T19:51:50.796006+00:00 → 2026-11-17T19:51:58.953419+00:00.
- Exit 0; 8.156 seconds; timeout=False.
- cwd: `.`; command: `[".tmp\\release-readiness\\20261118\\installed312\\Scripts\\python.exe", "-m", "pip", "install", "--force-reinstall", "--no-deps", ".tmp/release-readiness/20261118/dist-final/syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `fresh-wheel-reinstall-final.json`, `fresh-wheel-reinstall-final.log`; log SHA-256 `c981cfeba52bb80cedaeed7693013ac6f895bc079336969537d9fd3e073be3fd`.

### distribution-verify-final

- Start/end: 2026-11-17T19:51:59.359461+00:00 → 2026-11-17T19:51:59.712821+00:00.
- Exit 0; 0.344 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/verify_distribution.py", ".tmp/release-readiness/20261118/dist-final"]`.
- Evidence: `distribution-verify-final.json`, `distribution-verify-final.log`; log SHA-256 `6bb0b9d648e8294d2b47d4cb0d59c7b13529b461694c7b172ae69feab4929c50`.

### soak-quick

- Start/end: 2026-11-17T19:52:03.688449+00:00 → 2026-11-17T19:52:27.087023+00:00.
- Exit 0; 23.391 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.cli", "soak", "--profile", "quick", "--json"]`.
- Evidence: `soak-quick.json`, `soak-quick.log`; log SHA-256 `1367ed22a5fe77a230c249b4cdd2af3b83d4db2b3310148f1cec66282b7e7f11`.

### soak-release

- Start/end: 2026-11-17T19:52:27.276023+00:00 → 2026-11-17T20:02:33.769836+00:00.
- Exit 0; 606.485 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.cli", "soak", "--profile", "release-candidate", "--json"]`.
- Evidence: `soak-release.json`, `soak-release.log`; log SHA-256 `140deedd1b1353b31a947e97a3b5a292b981f59e3e9caf045ce774174e821f20`.

### folio-chromium

- Start/end: 2026-11-17T19:52:36.092019+00:00 → 2026-11-17T19:54:01.933762+00:00.
- Exit 0; 85.843 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-chromium.json`, `folio-chromium.log`; log SHA-256 `fd044d399b5ca6cfd7cc92723408bacfef6d1d5ee0a2cbaa237417ce6e854e3f`.

### connected-repair-installed

- Start/end: 2026-11-17T19:52:46.265552+00:00 → 2026-11-17T19:53:10.541317+00:00.
- Exit 0; 24.265 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "agentbus.repair.integration.acceptance", "--python", ".tmp/release-readiness/20261118/installed312/Scripts/python.exe", "--output", ".tmp/release-readiness/20261118/connected-installed"]`.
- Evidence: `connected-repair-installed.json`, `connected-repair-installed.log`; log SHA-256 `c985ef08b4baba1ee76894bbd36d0cc08ae2311dc3c6d39f090dcb43061fbf13`.

### repair-chromium

- Start/end: 2026-11-17T19:54:02.120454+00:00 → 2026-11-17T19:54:23.896957+00:00.
- Exit 0; 21.766 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-chromium.json`, `repair-chromium.log`; log SHA-256 `9c3dbba0b20933b020a2dfad5cc744bb98c8d600dbfece92a17d1ce280c9e29b`.

### folio-firefox

- Start/end: 2026-11-17T19:54:24.099015+00:00 → 2026-11-17T19:55:58.597181+00:00.
- Exit 0; 94.5 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-firefox.json`, `folio-firefox.log`; log SHA-256 `d21a4374f63a33d9b06cca9a990bcadb3474a0752bdbc79ce010f5cc64fd4109`.

### install-resource-probe

- Start/end: 2026-11-17T19:54:33.985183+00:00 → 2026-11-17T19:54:40.098096+00:00.
- Exit 0; 6.11 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pip", "install", "psutil"]`.
- Evidence: `install-resource-probe.json`, `install-resource-probe.log`; log SHA-256 `1df76006f33d993a9afe11e20ca4e45c7925b7003bf6e212f3ce59a11e7e9b1e`.

### http-load

- Start/end: 2026-11-17T19:55:07.209975+00:00 → 2026-11-17T19:55:15.385379+00:00.
- Exit 0; 8.188 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", ".tmp/release-readiness/20261118/http_load.py"]`.
- Evidence: `http-load.json`, `http-load.log`; log SHA-256 `ce2fee9e9fa41ccaee73602a8fa90c092bd76178f29391d36d2b53b7e35a11f5`.

### repair-firefox

- Start/end: 2026-11-17T19:55:58.777342+00:00 → 2026-11-17T19:56:29.031843+00:00.
- Exit 0; 30.25 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-firefox.json`, `repair-firefox.log`; log SHA-256 `9c3dbba0b20933b020a2dfad5cc744bb98c8d600dbfece92a17d1ce280c9e29b`.

### http-load-settled

- Start/end: 2026-11-17T19:56:04.067276+00:00 → 2026-11-17T19:56:18.326894+00:00.
- Exit 0; 14.266 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", ".tmp/release-readiness/20261118/http_load.py"]`.
- Evidence: `http-load-settled.json`, `http-load-settled.log`; log SHA-256 `7ab95cd4205be6dd1b25bfd4a942d75d8137706530bcba73e60d9509e4566443`.

### folio-webkit

- Start/end: 2026-11-17T19:56:29.204572+00:00 → 2026-11-17T19:57:11.792466+00:00.
- Exit 1; 42.594 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-webkit.json`, `folio-webkit.log`; log SHA-256 `ef3592ec45cd05ef09455e36b28f20909f0f39909c749e2969e374e2dc3fbbbc`.

### mutation-probe

- Start/end: 2026-11-17T19:57:08.534920+00:00 → 2026-11-17T19:57:11.406651+00:00.
- Exit 1; 2.875 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", ".tmp/release-readiness/20261118/mutation_probe.py"]`.
- Evidence: `mutation-probe.json`, `mutation-probe.log`; log SHA-256 `466fd16b9538d7b2db0e52a4e0cc916662983c85c9f08a1042128353e38dfd4f`.

### repair-webkit

- Start/end: 2026-11-17T19:57:11.975910+00:00 → 2026-11-17T19:57:36.910028+00:00.
- Exit 1; 24.937 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-webkit.json`, `repair-webkit.log`; log SHA-256 `d09cacd977843066c9bc28e7018f3fd402babc45756756a1774daea85a20e8c1`.

### policy-cold-import-red

- Start/end: 2026-11-17T19:57:32.267098+00:00 → 2026-11-17T19:57:32.871519+00:00.
- Exit 1; 0.609 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-c", "import agentbus.policy"]`.
- Evidence: `policy-cold-import-red.json`, `policy-cold-import-red.log`; log SHA-256 `641f9f933c48ad71c3a82bb483c393d98b00062b8a27d2e6fef2f6fbb3089528`.

### folio-msedge

- Start/end: 2026-11-17T19:57:37.110375+00:00 → 2026-11-17T19:59:05.487731+00:00.
- Exit 0; 88.375 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-msedge.json`, `folio-msedge.log`; log SHA-256 `7c706350931364df1c588d1ea8df943cf5527267612642c85a1cfbd61e3b8605`.

### policy-cold-import-green

- Start/end: 2026-11-17T19:58:09.022563+00:00 → 2026-11-17T19:58:13.183397+00:00.
- Exit 0; 4.171 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_public_import_order.py", "tests/test_tool_policy.py", "-q", "--basetemp=.tmp/release-readiness/20261118/cold-import", "--junitxml=.tmp/release-readiness/20261118/cold-import.xml"]`.
- Evidence: `policy-cold-import-green.json`, `policy-cold-import-green.log`; log SHA-256 `6491d7a7bc29f0bf16c5d9140cd44e6c840d0c3d0c69f81bf3fd829ca53fe6c4`.

### folio-webkit-diagnostic

- Start/end: 2026-11-17T19:58:29.649481+00:00 → 2026-11-17T19:59:18.363039+00:00.
- Exit 1; 48.718 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-webkit-diagnostic.json`, `folio-webkit-diagnostic.log`; log SHA-256 `f32c0c55cd9ee6e15c9348851c16c04687f76dfbe1f2ff62c6830249ee46415e`.

### mutation-probe-fixed

- Start/end: 2026-11-17T19:58:39.815510+00:00 → 2026-11-17T19:58:49.348454+00:00.
- Exit 0; 9.532 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", ".tmp/release-readiness/20261118/mutation_probe.py"]`.
- Evidence: `mutation-probe-fixed.json`, `mutation-probe-fixed.log`; log SHA-256 `380320e92e210d09c6631a83b47e383432f80406762c3e8d0633807e2364ff2a`.

### repair-msedge

- Start/end: 2026-11-17T19:59:05.700361+00:00 → 2026-11-17T19:59:30.847707+00:00.
- Exit 0; 25.141 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-msedge.json`, `repair-msedge.log`; log SHA-256 `9c3dbba0b20933b020a2dfad5cc744bb98c8d600dbfece92a17d1ce280c9e29b`.

### showcase-smoke-final

- Start/end: 2026-11-17T19:59:31.043080+00:00 → 2026-11-17T19:59:40.853489+00:00.
- Exit 0; 9.797 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "test:showcase"]`.
- Evidence: `showcase-smoke-final.json`, `showcase-smoke-final.log`; log SHA-256 `6c8671adfd408d6a541bf374969a2dd2d5d08a4ffbfa03bb783d2c15d9f32630`.

### studio-build-webkit-fix

- Start/end: 2026-11-17T20:01:27.863008+00:00 → 2026-11-17T20:01:37.592453+00:00.
- Exit 0; 9.735 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "build"]`.
- Evidence: `studio-build-webkit-fix.json`, `studio-build-webkit-fix.log`; log SHA-256 `ff67a094807b3dcba0d0bfb41f55e6d32551483d1eef4c423528ed9346dca509`.

### control-fixed-312-2

- Start/end: 2026-11-17T20:01:37.985493+00:00 → 2026-11-17T20:03:53.135661+00:00.
- Exit 0; 135.157 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.control.acceptance"]`.
- Evidence: `control-fixed-312-2.json`, `control-fixed-312-2.log`; log SHA-256 `56546256bb2a9bc6940e474d8272ca586fe590e5be7c7452b795e88401155de4`.

### distribution-build-candidate

- Start/end: 2026-11-17T20:02:03.602440+00:00 → 2026-11-17T20:02:38.079322+00:00.
- Exit 0; 34.484 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/dist-candidate"]`.
- Evidence: `distribution-build-candidate.json`, `distribution-build-candidate.log`; log SHA-256 `4c80de25727d9186a6becac84faa56d062f9a1654c45c8600ea7f66a59ce1ecf`.

### fresh-wheel-update-candidate

- Start/end: 2026-11-17T20:03:19.267171+00:00 → 2026-11-17T20:03:26.075569+00:00.
- Exit 0; 6.812 seconds; timeout=False.
- cwd: `.`; command: `[".tmp\\release-readiness\\20261118\\installed312\\Scripts\\python.exe", "-m", "pip", "install", "--force-reinstall", "--no-deps", ".tmp/release-readiness/20261118/dist-candidate/syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `fresh-wheel-update-candidate.json`, `fresh-wheel-update-candidate.log`; log SHA-256 `f8f1ab26481a91b244960024264662c938439758086cb7d94991cb05a4d04e4a`.

### soak-extended

- Start/end: 2026-11-17T20:03:26.446953+00:00 → 2026-11-17T21:03:44.322962+00:00.
- Exit 1; 3617.891 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.cli", "soak", "--profile", "release-candidate", "--duration", "3600", "--runs", "10000", "--json"]`.
- Evidence: `soak-extended.json`, `soak-extended.log`; log SHA-256 `11f66d021a1d42e56ec6bffea2d5c7e3670d1195de12530eb2323366918eeed5`.

### repair-webkit-fixed

- Start/end: 2026-11-17T20:03:50.053152+00:00 → 2026-11-17T20:04:38.666853+00:00.
- Exit 1; 48.625 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-webkit-fixed.json`, `repair-webkit-fixed.log`; log SHA-256 `d09cacd977843066c9bc28e7018f3fd402babc45756756a1774daea85a20e8c1`.

### folio-webkit-fixed

- Start/end: 2026-11-17T20:03:50.066216+00:00 → 2026-11-17T20:05:40.209047+00:00.
- Exit 1; 110.141 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-webkit-fixed.json`, `folio-webkit-fixed.log`; log SHA-256 `831aa3682435219905f75fb59b56ed052151918a2c8499df1c49b3523b7ed92b`.

### extension-crossclient-compile

- Start/end: 2026-11-17T20:06:07.125565+00:00 → 2026-11-17T20:06:10.858273+00:00.
- Exit 0; 3.734 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "compile"]`.
- Evidence: `extension-crossclient-compile.json`, `extension-crossclient-compile.log`; log SHA-256 `b6b7e42efd841d43cd7988a70f3eb717e0d32dab3397622309f5a5c0aa84bf5d`.

### cross-client-electron

- Start/end: 2026-11-17T20:07:21.704047+00:00 → 2026-11-17T20:09:29.477540+00:00.
- Exit 1; 127.766 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "test:integration"]`.
- Evidence: `cross-client-electron.json`, `cross-client-electron.log`; log SHA-256 `f0f0bd34219027a0e8605c3a26e9100e715ede5a3efa85d454a49576b9d21928`.

### repair-webkit-diagnostic2

- Start/end: 2026-11-17T20:07:31.952497+00:00 → 2026-11-17T20:08:16.978899+00:00.
- Exit 1; 45.031 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-webkit-diagnostic2.json`, `repair-webkit-diagnostic2.log`; log SHA-256 `d09cacd977843066c9bc28e7018f3fd402babc45756756a1774daea85a20e8c1`.

### python-3.13-install

- Start/end: 2026-11-17T20:08:42.035320+00:00 → 2026-11-17T20:09:29.015351+00:00.
- Exit 0; 46.985 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed313\\Scripts\\python.exe", "-m", "pip", "install", "<checkout>\\.tmp\\release-readiness\\20261118\\dist-candidate\\syndra-0.6.0b1-py3-none-any.whl[dev,ide,mcp]"]`.
- Evidence: `python-3.13-install.json`, `python-3.13-install.log`; log SHA-256 `d5a503c7abf208631eb03fce113f7de29d14106b504486db9816298bd483c9ac`.

### python-3.14-install

- Start/end: 2026-11-17T20:08:42.100992+00:00 → 2026-11-17T20:09:28.686989+00:00.
- Exit 0; 46.593 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed314\\Scripts\\python.exe", "-m", "pip", "install", "<checkout>\\.tmp\\release-readiness\\20261118\\dist-candidate\\syndra-0.6.0b1-py3-none-any.whl[dev,ide,mcp]"]`.
- Evidence: `python-3.14-install.json`, `python-3.14-install.log`; log SHA-256 `3c2320d4bf68330619cdc492a82d85a6e471723a203f083a1c1c61e0a45aba96`.

### python-3.11-install

- Start/end: 2026-11-17T20:08:48.035360+00:00 → 2026-11-17T20:09:30.237177+00:00.
- Exit 0; 42.203 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed311\\Scripts\\python.exe", "-m", "pip", "install", "<checkout>\\.tmp\\release-readiness\\20261118\\dist-candidate\\syndra-0.6.0b1-py3-none-any.whl[dev,ide,mcp]"]`.
- Evidence: `python-3.11-install.json`, `python-3.11-install.log`; log SHA-256 `b345107bc8686a96e3f890d33964aacabe643f1713f0ee17317d3db5a8d27797`.

### studio-lint-final

- Start/end: 2026-11-17T20:09:24.621408+00:00 → 2026-11-17T20:09:44.266344+00:00.
- Exit 0; 19.641 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "lint"]`.
- Evidence: `studio-lint-final.json`, `studio-lint-final.log`; log SHA-256 `bbf3a59c9d157ed5e71457fcbec7947d2fe8c01cda8ffb8e529e15bf4be67362`.

### python-3.14-origin

- Start/end: 2026-11-17T20:09:28.999250+00:00 → 2026-11-17T20:09:30.436431+00:00.
- Exit 0; 1.437 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed314\\Scripts\\python.exe", "-I", "-c", "import sys,syndra,agentbus.policy;from pathlib import Path;assert Path(syndra.__file__).resolve().is_relative_to(Path(sys.prefix).resolve());print(sys.version);print(syndra.__version__)"]`.
- Evidence: `python-3.14-origin.json`, `python-3.14-origin.log`; log SHA-256 `b44c37660305977f1e5f959a0aba6d719dc62199b15f32b55ca154dad8ed7150`.

### python-3.13-origin

- Start/end: 2026-11-17T20:09:29.323266+00:00 → 2026-11-17T20:09:30.513651+00:00.
- Exit 0; 1.188 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed313\\Scripts\\python.exe", "-I", "-c", "import sys,syndra,agentbus.policy;from pathlib import Path;assert Path(syndra.__file__).resolve().is_relative_to(Path(sys.prefix).resolve());print(sys.version);print(syndra.__version__)"]`.
- Evidence: `python-3.13-origin.json`, `python-3.13-origin.log`; log SHA-256 `9287c437c976b7c44b06759a37ff852885ad344b3badc7d218645036b72cfcee`.

### python-3.11-origin

- Start/end: 2026-11-17T20:09:30.516651+00:00 → 2026-11-17T20:09:31.637179+00:00.
- Exit 0; 1.125 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed311\\Scripts\\python.exe", "-I", "-c", "import sys,syndra,agentbus.policy;from pathlib import Path;assert Path(syndra.__file__).resolve().is_relative_to(Path(sys.prefix).resolve());print(sys.version);print(syndra.__version__)"]`.
- Evidence: `python-3.11-origin.json`, `python-3.11-origin.log`; log SHA-256 `d3a53dd7d6e9b9bb6df2c520ecbe5a6a16e9743910787cedec59c66f5d024615`.

### python-3.14-quickstart

- Start/end: 2026-11-17T20:09:30.708076+00:00 → 2026-11-17T20:09:35.084289+00:00.
- Exit 0; 4.375 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed314\\Scripts\\python.exe", "-I", "-m", "syndra.cli", "quickstart", "--json"]`.
- Evidence: `python-3.14-quickstart.json`, `python-3.14-quickstart.log`; log SHA-256 `9e4f39861e7f41f0ccb9a8e806c695da0ae5ee6dbad879e1df7c1b65100d01ca`.

### python-3.13-quickstart

- Start/end: 2026-11-17T20:09:30.786971+00:00 → 2026-11-17T20:09:35.556561+00:00.
- Exit 0; 4.766 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed313\\Scripts\\python.exe", "-I", "-m", "syndra.cli", "quickstart", "--json"]`.
- Evidence: `python-3.13-quickstart.json`, `python-3.13-quickstart.log`; log SHA-256 `d37c709005cf43fb25b2885c4acb7dab40a03264a801490afc575daa4db94fe9`.

### python-3.11-quickstart

- Start/end: 2026-11-17T20:09:31.921787+00:00 → 2026-11-17T20:09:36.746526+00:00.
- Exit 0; 4.828 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed311\\Scripts\\python.exe", "-I", "-m", "syndra.cli", "quickstart", "--json"]`.
- Evidence: `python-3.11-quickstart.json`, `python-3.11-quickstart.log`; log SHA-256 `972ae5e38236b83ee72e823922802367423d7d269e535c076ef09d711c305de1`.

### python-3.14-core-boundaries

- Start/end: 2026-11-17T20:09:35.368330+00:00 → 2026-11-17T20:09:50.411395+00:00.
- Exit 0; 15.047 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed314\\Scripts\\python.exe", "-m", "pytest", "tests/test_tool_policy.py", "tests/test_control_supervisor.py", "tests/test_control_admission.py", "tests/test_mcp_stdio_transport.py", "tests/test_sandbox_process.py", "tests/test_filesystem_security.py", "tests/property/test_runtime_state_properties.py", "-q", "--basetemp", "<checkout>\\.tmp\\release-readiness\\20261118\\platform-3.14-tmp", "--junitxml", "<checkout>\\.tmp\\release-readiness\\20261118\\platform-3.14.xml"]`.
- Evidence: `python-3.14-core-boundaries.json`, `python-3.14-core-boundaries.log`; log SHA-256 `6246c9ec495cbf1dd07e946726ee404135a3af39ea8153003dde69ea03f2a755`.

### python-3.13-core-boundaries

- Start/end: 2026-11-17T20:09:35.854370+00:00 → 2026-11-17T20:09:50.834314+00:00.
- Exit 0; 14.969 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed313\\Scripts\\python.exe", "-m", "pytest", "tests/test_tool_policy.py", "tests/test_control_supervisor.py", "tests/test_control_admission.py", "tests/test_mcp_stdio_transport.py", "tests/test_sandbox_process.py", "tests/test_filesystem_security.py", "tests/property/test_runtime_state_properties.py", "-q", "--basetemp", "<checkout>\\.tmp\\release-readiness\\20261118\\platform-3.13-tmp", "--junitxml", "<checkout>\\.tmp\\release-readiness\\20261118\\platform-3.13.xml"]`.
- Evidence: `python-3.13-core-boundaries.json`, `python-3.13-core-boundaries.log`; log SHA-256 `1a0339467607054f3ef286b82de9085b49ab46543f062d5a0bf818e530806649`.

### python-3.11-core-boundaries

- Start/end: 2026-11-17T20:09:37.038349+00:00 → 2026-11-17T20:09:51.155502+00:00.
- Exit 1; 14.125 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed311\\Scripts\\python.exe", "-m", "pytest", "tests/test_tool_policy.py", "tests/test_control_supervisor.py", "tests/test_control_admission.py", "tests/test_mcp_stdio_transport.py", "tests/test_sandbox_process.py", "tests/test_filesystem_security.py", "tests/property/test_runtime_state_properties.py", "-q", "--basetemp", "<checkout>\\.tmp\\release-readiness\\20261118\\platform-3.11-tmp", "--junitxml", "<checkout>\\.tmp\\release-readiness\\20261118\\platform-3.11.xml"]`.
- Evidence: `python-3.11-core-boundaries.json`, `python-3.11-core-boundaries.log`; log SHA-256 `b142039bfa70a4145b016c4cac4c0b9ceafb7fb2062f50718993fd87f409b970`.

### deterministic-env-red

- Start/end: 2026-11-17T20:12:19.540439+00:00 → 2026-11-17T20:12:21.307171+00:00.
- Exit 1; 1.781 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_product_config.py", "-k", "deterministic_environment", "-q", "--basetemp=.tmp/release-readiness/20261118/env-red"]`.
- Evidence: `deterministic-env-red.json`, `deterministic-env-red.log`; log SHA-256 `36e0a87f5bce14580a23b9db17dcadec3e00fcfb54c9ba24dbf6bec90fc99980`.

### deterministic-env-green

- Start/end: 2026-11-17T20:12:50.450006+00:00 → 2026-11-17T20:12:53.634613+00:00.
- Exit 0; 3.172 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_product_config.py", "tests/test_config.py", "tests/test_config_cli.py", "tests/test_config_store.py", "tests/test_deterministic_provider.py", "-q", "--basetemp=.tmp/release-readiness/20261118/env-green", "--junitxml=.tmp/release-readiness/20261118/env-green.xml"]`.
- Evidence: `deterministic-env-green.json`, `deterministic-env-green.log`; log SHA-256 `c5f192787ed1fceecb552a190fd4a90b383791917f5e3d1402e36476c16f3da2`.

### crossclient-compile-final

- Start/end: 2026-11-17T20:13:16.734821+00:00 → 2026-11-17T20:13:20.248531+00:00.
- Exit 0; 3.516 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "compile"]`.
- Evidence: `crossclient-compile-final.json`, `crossclient-compile-final.log`; log SHA-256 `b6b7e42efd841d43cd7988a70f3eb717e0d32dab3397622309f5a5c0aa84bf5d`.

### native-quota-regression-312

- Start/end: 2026-11-17T20:15:33.323873+00:00 → 2026-11-17T20:15:38.129384+00:00.
- Exit 0; 4.797 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_sandbox_process.py", "tests/test_sandbox_process_stress.py", "-q", "--basetemp=.tmp/release-readiness/20261118/quota-312", "--junitxml=.tmp/release-readiness/20261118/quota-312.xml"]`.
- Evidence: `native-quota-regression-312.json`, `native-quota-regression-312.log`; log SHA-256 `890db73897c0206715f512e9a3aa9224ed248d63c55ff35d8ae48ecfedbf01ac`.

### native-quota-regression-311

- Start/end: 2026-11-17T20:15:38.644474+00:00 → 2026-11-17T20:15:45.151944+00:00.
- Exit 0; 6.515 seconds; timeout=False.
- cwd: `.`; command: `[".tmp\\release-readiness\\20261118\\installed311\\Scripts\\python.exe", "-m", "pytest", "tests/test_sandbox_process.py", "tests/test_sandbox_process_stress.py", "-q", "--basetemp=.tmp/release-readiness/20261118/quota-311", "--junitxml=.tmp/release-readiness/20261118/quota-311.xml"]`.
- Evidence: `native-quota-regression-311.json`, `native-quota-regression-311.log`; log SHA-256 `ba95bd8684720f3059a093756aa66c28b2498e5762282253cd064c8103dc539b`.

### studio-build-candidate

- Start/end: 2026-11-17T20:15:45.681245+00:00 → 2026-11-17T20:15:55.253256+00:00.
- Exit 0; 9.578 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "build"]`.
- Evidence: `studio-build-candidate.json`, `studio-build-candidate.log`; log SHA-256 `fe327cba0c0e6a95b25d33f874bf8c37641b826537aaf56c82b218fd412f7bcd`.

### release-check-full

- Start/end: 2026-11-17T20:16:20.343842+00:00 → 2026-11-17T20:47:17.267778+00:00.
- Exit 1; 1856.922 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.cli", "release-check", "--full", "--json"]`.
- Evidence: `release-check-full.json`, `release-check-full.log`; log SHA-256 `7e0b2594d99b6b956d4fbf020916cdf9a2f34945293cd375b36fa265e61238da`.

### build-pilot-candidate

- Start/end: 2026-11-17T20:16:29.478557+00:00 → 2026-11-17T20:17:00.478585+00:00.
- Exit 0; 31.0 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/pilot-candidate"]`.
- Evidence: `build-pilot-candidate.json`, `build-pilot-candidate.log`; log SHA-256 `b83944fb03bf7cb2823425e10d5b960f6877e684499e641a7f94997e0dbae4c7`.

### cross-client-electron-final

- Start/end: 2026-11-17T20:16:39.756990+00:00 → 2026-11-17T20:18:06.647195+00:00.
- Exit 1; 86.891 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "test:integration"]`.
- Evidence: `cross-client-electron-final.json`, `cross-client-electron-final.log`; log SHA-256 `e045bda85893644a37309d84f78872d781ffde5cf4cdbd6bbfb832d72e86a112`.

### studio-unit-candidate

- Start/end: 2026-11-17T20:18:10.461714+00:00 → 2026-11-17T20:21:12.210193+00:00.
- Exit 0; 181.75 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "test"]`.
- Evidence: `studio-unit-candidate.json`, `studio-unit-candidate.log`; log SHA-256 `34be61c7f5747564c0d5b9e257987bae6c714131c2e31e8acf64d4789c6dc6ef`.

### python-compile-final

- Start/end: 2026-11-17T20:18:20.659279+00:00 → 2026-11-17T20:18:21.154607+00:00.
- Exit 0; 0.5 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "compileall", "-q", "syndra", "agentbus"]`.
- Evidence: `python-compile-final.json`, `python-compile-final.log`; log SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

### update-installed-webkit-candidate

- Start/end: 2026-11-17T20:18:46.018650+00:00 → 2026-11-17T20:18:54.009124+00:00.
- Exit 0; 7.984 seconds; timeout=False.
- cwd: `.`; command: `[".tmp\\release-readiness\\20261118\\installed312\\Scripts\\python.exe", "-m", "pip", "install", "--force-reinstall", "--no-deps", ".tmp/release-readiness/20261118/pilot-candidate/syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `update-installed-webkit-candidate.json`, `update-installed-webkit-candidate.log`; log SHA-256 `77cecf910a810bfdb0f0616261b299d59330e2402d468593f59d68e3d899eeb8`.

### cross-client-electron-proof

- Start/end: 2026-11-17T20:20:25.506108+00:00 → 2026-11-17T20:22:50.285911+00:00.
- Exit 0; 144.781 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "test:integration"]`.
- Evidence: `cross-client-electron-proof.json`, `cross-client-electron-proof.log`; log SHA-256 `a76bcb66b632e5474ec9d2f6a890c577f57bb5aa616d44290e056afaa96d0bba`.

### repair-webkit-candidate

- Start/end: 2026-11-17T20:20:25.506108+00:00 → 2026-11-17T20:22:05.237180+00:00.
- Exit 0; 99.734 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-webkit-candidate.json`, `repair-webkit-candidate.log`; log SHA-256 `9c3dbba0b20933b020a2dfad5cc744bb98c8d600dbfece92a17d1ce280c9e29b`.

### folio-webkit-candidate

- Start/end: 2026-11-17T20:20:25.511796+00:00 → 2026-11-17T20:22:15.884322+00:00.
- Exit 1; 110.359 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-webkit-candidate.json`, `folio-webkit-candidate.log`; log SHA-256 `aa8b90d128aab64880579220f79b9e5542279dbdf2b79700c834809f87ac4583`.

### lifecycle-privacy

- Start/end: 2026-11-17T20:23:00.816234+00:00 → 2026-11-17T20:23:22.740951+00:00.
- Exit 0; 21.922 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_supervisor.py", "tests/test_control_mcp_owner_recovery.py", "tests/test_control_admission.py", "-q", "--basetemp=.tmp/release-readiness/20261118/lifecycle-privacy", "--junitxml=.tmp/release-readiness/20261118/lifecycle-privacy.xml"]`.
- Evidence: `lifecycle-privacy.json`, `lifecycle-privacy.log`; log SHA-256 `5577c56c97bc3bf8326eb008de6add4d78524b66dc4205c78aa9bb6acc204aef`.

### folio-webkit-isolated

- Start/end: 2026-11-17T20:24:16.241713+00:00 → 2026-11-17T20:26:16.689620+00:00.
- Exit 1; 120.453 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-webkit-isolated.json`, `folio-webkit-isolated.log`; log SHA-256 `831aa3682435219905f75fb59b56ed052151918a2c8499df1c49b3523b7ed92b`.

### extension-lint-final

- Start/end: 2026-11-17T20:24:26.410817+00:00 → 2026-11-17T20:24:36.275457+00:00.
- Exit 0; 9.86 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "lint"]`.
- Evidence: `extension-lint-final.json`, `extension-lint-final.log`; log SHA-256 `657ef0843b7ba26bbbdc9c9d3b99b90baaae5ccfcdd52726d2e369d5b697d99f`.

### benchmark-baseline

- Start/end: 2026-11-17T20:26:00.322003+00:00 → 2026-11-17T20:27:07.292292+00:00.
- Exit 0; 66.985 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.cli", "benchmark", "all", "--files", "250", "--iterations", "5", "--output", ".tmp/release-readiness/20261118/benchmark-baseline-results.json", "--json"]`.
- Evidence: `benchmark-baseline.json`, `benchmark-baseline.log`; log SHA-256 `356a4e8a266627a02d6c0ee29b16e7c3bf20490b5f1459f48d6596ac5369a2a9`.

### final-source-regressions

- Start/end: 2026-11-17T20:26:10.484731+00:00 → 2026-11-17T20:26:59.184532+00:00.
- Exit 0; 48.703 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_supervisor.py", "tests/test_control_mcp_owner_recovery.py", "tests/test_control_admission.py", "tests/test_product_config.py", "tests/test_public_import_order.py", "tests/test_repository_intelligence_evaluation.py", "tests/test_customer_repair.py", "tests/test_ci_release_gates.py", "tests/test_sandbox_process.py", "tests/test_branding.py", "-q", "--basetemp=.tmp/release-readiness/20261118/final-source-regressions", "--junitxml=.tmp/release-readiness/20261118/final-source-regressions.xml"]`.
- Evidence: `final-source-regressions.json`, `final-source-regressions.log`; log SHA-256 `1dd825488ea708cf9f49ffda00181f44f69ee5962f82a475c632d7a0f76e5fdc`.

### benchmark-compare

- Start/end: 2026-11-17T20:27:07.472958+00:00 → 2026-11-17T20:28:11.693292+00:00.
- Exit 0; 64.218 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.cli", "benchmark", "all", "--files", "250", "--iterations", "5", "--baseline", ".tmp/release-readiness/20261118/benchmark-baseline-results.json", "--output", ".tmp/release-readiness/20261118/benchmark-compare-results.json", "--json"]`.
- Evidence: `benchmark-compare.json`, `benchmark-compare.log`; log SHA-256 `59b5a970c4c3ca48d8bc521754c65cf899f940b83f0ab7b881bb5ebd44572775`.

### folio-webkit-confirm

- Start/end: 2026-11-17T20:27:15.307406+00:00 → 2026-11-17T20:28:36.827764+00:00.
- Exit 1; 81.516 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-webkit-confirm.json`, `folio-webkit-confirm.log`; log SHA-256 `6ab56e8e83e515c93fda9e708c5c5092c742e68c0e6baf1ea9f153b28acea696`.

### index-scale-medium

- Start/end: 2026-11-17T20:28:11.903613+00:00 → 2026-11-17T20:30:07.155195+00:00.
- Exit 0; 115.25 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.cli", "benchmark", "index-scale", "--size", "medium", "--json"]`.
- Evidence: `index-scale-medium.json`, `index-scale-medium.log`; log SHA-256 `cd024d60456bc967ad799cc0380c5ca46fc8117f4bdfa3c813fdcbf44da4bd44`.

### index-scale-large

- Start/end: 2026-11-17T20:30:07.348165+00:00 → 2026-11-17T20:54:41.230797+00:00.
- Exit 0; 1473.89 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.cli", "benchmark", "index-scale", "--size", "large", "--json"]`.
- Evidence: `index-scale-large.json`, `index-scale-large.log`; log SHA-256 `acbbf974206d4ed93ccf041a83fec7a37930c7d10da1d57154d8a0aac48be1df`.

### folio-webkit-scoped

- Start/end: 2026-11-17T20:35:22.101048+00:00 → 2026-11-17T20:37:41.797916+00:00.
- Exit 1; 139.703 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-webkit-scoped.json`, `folio-webkit-scoped.log`; log SHA-256 `24655ef6a077013bafa47b45366450f6a5f7688086fa298fa411234cfbb722d8`.

### real-daemon-crash

- Start/end: 2026-11-17T20:37:10.903928+00:00 → 2026-11-17T20:37:14.249455+00:00.
- Exit 2; 3.344 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_process_restart.py", "-q", "-s"]`.
- Evidence: `real-daemon-crash.json`, `real-daemon-crash.log`; log SHA-256 `43015e625cc6e7f23f7037ec5e78e474778b3b9e0476867ccb04dc136546e9a9`.

### real-daemon-crash-fixed

- Start/end: 2026-11-17T20:37:26.034954+00:00 → 2026-11-17T20:38:10.863715+00:00.
- Exit 1; 44.828 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_process_restart.py", "-q", "-s"]`.
- Evidence: `real-daemon-crash-fixed.json`, `real-daemon-crash-fixed.log`; log SHA-256 `7526182f9883c3caaaab9079bf190cdc25109e1ba1e2e5d6f807f0c13b26815c`.

### real-daemon-crash-verified

- Start/end: 2026-11-17T20:39:20.955883+00:00 → 2026-11-17T20:40:10.273625+00:00.
- Exit 1; 49.313 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_process_restart.py", "-q", "-s"]`.
- Evidence: `real-daemon-crash-verified.json`, `real-daemon-crash-verified.log`; log SHA-256 `73ca05e533080b17a3dd788bca89efd86f5e62b467413b8ee09679e1591b3332`.

### studio-stage-release

- Start/end: 2026-11-17T20:39:31.120408+00:00 → 2026-11-17T20:39:31.940037+00:00.
- Exit 0; 0.812 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/prepare_studio.py"]`.
- Evidence: `studio-stage-release.json`, `studio-stage-release.log`; log SHA-256 `30967d6dd0fd2ae66d99c74eee88dda27504bb2278d4c39f612fd95e1195b1b5`.

### theme-candidate-studio-build

- Start/end: 2026-11-17T20:39:54.142500+00:00 → 2026-11-17T20:40:06.035793+00:00.
- Exit 0; 11.89 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "build"]`.
- Evidence: `theme-candidate-studio-build.json`, `theme-candidate-studio-build.log`; log SHA-256 `390514c9e9a2ca21eab1d754c247e97aa4f925c22036402f7d744a254bf1f05a`.

### theme-candidate-stage

- Start/end: 2026-11-17T20:40:06.251556+00:00 → 2026-11-17T20:40:07.234543+00:00.
- Exit 0; 0.984 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/prepare_studio.py"]`.
- Evidence: `theme-candidate-stage.json`, `theme-candidate-stage.log`; log SHA-256 `30967d6dd0fd2ae66d99c74eee88dda27504bb2278d4c39f612fd95e1195b1b5`.

### theme-candidate-build

- Start/end: 2026-11-17T20:40:07.423157+00:00 → 2026-11-17T20:40:38.726929+00:00.
- Exit 0; 31.297 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/theme-candidate"]`.
- Evidence: `theme-candidate-build.json`, `theme-candidate-build.log`; log SHA-256 `03f4539342aad6af20d870f3bc530b70196ac7ab46d527386756cc589eec0f1e`.

### theme-candidate-verify

- Start/end: 2026-11-17T20:40:38.958590+00:00 → 2026-11-17T20:40:39.320019+00:00.
- Exit 0; 0.359 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/verify_distribution.py", ".tmp/release-readiness/20261118/theme-candidate"]`.
- Evidence: `theme-candidate-verify.json`, `theme-candidate-verify.log`; log SHA-256 `19b9663eabd9eef4df2dbd12cf4f24e978969c26309e7a979aaeea6f4236bc68`.

### theme-candidate-install

- Start/end: 2026-11-17T20:40:39.538057+00:00 → 2026-11-17T20:40:47.563336+00:00.
- Exit 0; 8.031 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed312\\Scripts\\python.exe", "-m", "pip", "install", "--no-deps", "--force-reinstall", ".tmp/release-readiness/20261118/theme-candidate/syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `theme-candidate-install.json`, `theme-candidate-install.log`; log SHA-256 `0911f1213133d3ac68b2b6b79a63f0941d64809ff07bcd1bd39e59ba9fc4defc`.

### real-daemon-restart-policy

- Start/end: 2026-11-17T20:41:18.599885+00:00 → 2026-11-17T20:42:24.804317+00:00.
- Exit 1; 66.203 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_process_restart.py", "-q", "-s"]`.
- Evidence: `real-daemon-restart-policy.json`, `real-daemon-restart-policy.log`; log SHA-256 `d7ecdfa8ba36f6595f7c6dde726724676ce063ab4fc6a3cc99ca347833271ac1`.

### folio-webkit-theme

- Start/end: 2026-11-17T20:41:18.634171+00:00 → 2026-11-17T20:43:41.921605+00:00.
- Exit 1; 143.297 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-webkit-theme.json`, `folio-webkit-theme.log`; log SHA-256 `24655ef6a077013bafa47b45366450f6a5f7688086fa298fa411234cfbb722d8`.

### real-daemon-restart-budgets

- Start/end: 2026-11-17T20:42:47.593909+00:00 → 2026-11-17T20:43:52.611388+00:00.
- Exit 1; 65.015 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_process_restart.py", "-q", "-s"]`.
- Evidence: `real-daemon-restart-budgets.json`, `real-daemon-restart-budgets.log`; log SHA-256 `3e9e15253bd917da226fad6a3e5da3bfc7b2dd19527598a6a2285ef83d7e0056`.

### studio-audit-final

- Start/end: 2026-11-17T20:43:13.094599+00:00 → 2026-11-17T20:43:21.631735+00:00.
- Exit 1; 8.531 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "audit", "--json"]`.
- Evidence: `studio-audit-final.json`, `studio-audit-final.log`; log SHA-256 `ea1de111e5560891c4445e28dd29b1c8a5589b40ddd5f7462448e395fd8246de`.

### vscode-audit-final

- Start/end: 2026-11-17T20:43:21.884525+00:00 → 2026-11-17T20:43:27.057740+00:00.
- Exit 1; 5.187 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "audit", "--json"]`.
- Evidence: `vscode-audit-final.json`, `vscode-audit-final.log`; log SHA-256 `d0681290027367c6f9446f3577be5a42c38a2367de64a20fb270fab28bbbf6bc`.

### studio-runtime-audit

- Start/end: 2026-11-17T20:43:27.289793+00:00 → 2026-11-17T20:43:29.785051+00:00.
- Exit 0; 2.5 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "audit", "--omit=dev", "--json"]`.
- Evidence: `studio-runtime-audit.json`, `studio-runtime-audit.log`; log SHA-256 `3b86377b890553a9a2e853742a35c7f058055e3dbc5150651ebdec3f776fd641`.

### vscode-runtime-audit

- Start/end: 2026-11-17T20:43:30.037343+00:00 → 2026-11-17T20:43:32.113843+00:00.
- Exit 0; 2.078 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "audit", "--omit=dev", "--json"]`.
- Evidence: `vscode-runtime-audit.json`, `vscode-runtime-audit.log`; log SHA-256 `3c8bb244474cf9cb6681eae3f4e71c133829702ceb8977bc433caa7ee6508c6b`.

### pip-audit-final

- Start/end: 2026-11-17T20:43:32.403078+00:00 → 2026-11-17T20:43:40.611707+00:00.
- Exit 0; 8.203 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pip_audit", "--format=json"]`.
- Evidence: `pip-audit-final.json`, `pip-audit-final.log`; log SHA-256 `ead9d9cdf4dadb356c81301448424c132a852d4a86cec931104eef68c40a4670`.

### bandit-high-gate

- Start/end: 2026-11-17T20:43:40.836813+00:00 → 2026-11-17T20:43:50.330477+00:00.
- Exit 0; 9.5 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "bandit", "-r", "agentbus", "syndra", "-lll", "-ii", "-f", "json"]`.
- Evidence: `bandit-high-gate.json`, `bandit-high-gate.log`; log SHA-256 `fa6165912d47e06fd19f77635de94ada93690f36f93f06da988955309cf51ebd`.

### process-recovery-regression

- Start/end: 2026-11-17T20:46:16.991550+00:00 → 2026-11-17T20:48:49.069006+00:00.
- Exit 0; 152.078 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_process_restart.py", "tests/test_durable_retry_feedback.py", "tests/test_daemon_restart_matrix.py", "-q", "-s"]`.
- Evidence: `process-recovery-regression.json`, `process-recovery-regression.log`; log SHA-256 `261cbbfd2a2753a8317eb32e54e41d28c3e0fe386de59f9af31fff79847d30d3`.

### recovery-candidate-studio-build

- Start/end: 2026-11-17T20:46:27.290937+00:00 → 2026-11-17T20:46:37.521528+00:00.
- Exit 0; 10.235 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "build"]`.
- Evidence: `recovery-candidate-studio-build.json`, `recovery-candidate-studio-build.log`; log SHA-256 `6d4099d3fe978c03d4009734c8f2d6d34172962cce2ed0701711500992305e41`.

### recovery-candidate-stage

- Start/end: 2026-11-17T20:46:37.719309+00:00 → 2026-11-17T20:46:38.680662+00:00.
- Exit 0; 0.953 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/prepare_studio.py"]`.
- Evidence: `recovery-candidate-stage.json`, `recovery-candidate-stage.log`; log SHA-256 `30967d6dd0fd2ae66d99c74eee88dda27504bb2278d4c39f612fd95e1195b1b5`.

### recovery-candidate-build

- Start/end: 2026-11-17T20:46:38.862702+00:00 → 2026-11-17T20:46:42.929473+00:00.
- Exit 1; 4.063 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/recovery-candidate"]`.
- Evidence: `recovery-candidate-build.json`, `recovery-candidate-build.log`; log SHA-256 `0389ffdbf9fc0fed189b85be8e5341d0047a473b777d8cbe4e8bda2c81ebbe55`.

### pytest-complete-candidate

- Start/end: 2026-11-17T20:47:58.751925+00:00 → 2026-11-17T21:08:45.636860+00:00.
- Exit 1; 1246.891 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "-q", "--basetemp", ".tmp/release-readiness/20261118/full-complete-tmp", "--junitxml", ".tmp/release-readiness/20261118/full-complete.xml", "--durations=20"]`.
- Evidence: `pytest-complete-candidate.json`, `pytest-complete-candidate.log`; log SHA-256 `fc10a4879d0d212efe9e2de87b823b39f348bbf8d53bebe6bbb5e084da9861ea`.

### recovery-artifacts-build

- Start/end: 2026-11-17T20:48:37.763219+00:00 → 2026-11-17T20:49:06.428148+00:00.
- Exit 0; 28.656 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/recovery-artifacts"]`.
- Evidence: `recovery-artifacts-build.json`, `recovery-artifacts-build.log`; log SHA-256 `08db9d2641a25e69086ce2b6af2f0e1524b1288d6678e19ad27f6133d18edf17`.

### recovery-wheel-install

- Start/end: 2026-11-17T20:49:19.210627+00:00 → 2026-11-17T20:49:26.787674+00:00.
- Exit 0; 7.578 seconds; timeout=False.
- cwd: `.`; command: `[".tmp/release-readiness/20261118/installed312/Scripts/python.exe", "-m", "pip", "install", "--no-deps", "--force-reinstall", ".tmp/release-readiness/20261118/recovery-artifacts/syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `recovery-wheel-install.json`, `recovery-wheel-install.log`; log SHA-256 `e822273a1ade691ea31cb7236ef3789339b65013ac15f24f52c2d2c1f92c531c`.

### folio-webkit-explicit-theme

- Start/end: 2026-11-17T20:49:39.269374+00:00 → 2026-11-17T20:51:57.868118+00:00.
- Exit 1; 138.593 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-webkit-explicit-theme.json`, `folio-webkit-explicit-theme.log`; log SHA-256 `66bc75c69dc58a91b27ce63ea3da3fdcf72e7a9c71911da4c7817e730c383102`.

### installed-process-restart

- Start/end: 2026-11-17T20:49:49.420991+00:00 → 2026-11-17T20:51:37.089661+00:00.
- Exit 0; 107.672 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_control_process_restart.py", "-q", "-s", "--basetemp", ".tmp/release-readiness/20261118/installed-process-tmp"]`.
- Evidence: `installed-process-restart.json`, `installed-process-restart.log`; log SHA-256 `54b83891c650fb0c7eb77328f871d1b006cd282ea2513cec1dd5fb18e1cbb773`.

### folio-webkit-theme-rules

- Start/end: 2026-11-17T20:54:14.237465+00:00 → 2026-11-17T20:56:24.171268+00:00.
- Exit 1; 129.938 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-webkit-theme-rules.json`, `folio-webkit-theme-rules.log`; log SHA-256 `4ae9e8a280b8424410d146d74bcf377084174daccb670a4df7bbf600596e19d1`.

### inherit-candidate-studio-build

- Start/end: 2026-11-17T20:56:01.348559+00:00 → 2026-11-17T20:56:11.803649+00:00.
- Exit 0; 10.453 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "build"]`.
- Evidence: `inherit-candidate-studio-build.json`, `inherit-candidate-studio-build.log`; log SHA-256 `3d3f95b56ecbc2db454f46ccd8af5e949ffb5ba6eadfda5b699f25dd1728e987`.

### inherit-candidate-stage

- Start/end: 2026-11-17T20:56:12.008096+00:00 → 2026-11-17T20:56:13.197352+00:00.
- Exit 0; 1.187 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/prepare_studio.py"]`.
- Evidence: `inherit-candidate-stage.json`, `inherit-candidate-stage.log`; log SHA-256 `30967d6dd0fd2ae66d99c74eee88dda27504bb2278d4c39f612fd95e1195b1b5`.

### inherit-candidate-build

- Start/end: 2026-11-17T20:56:13.402115+00:00 → 2026-11-17T20:56:43.779602+00:00.
- Exit 0; 30.375 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/inherit-candidate"]`.
- Evidence: `inherit-candidate-build.json`, `inherit-candidate-build.log`; log SHA-256 `9064a77acd5a658d32a9b9a3df04bf4fb43812ca7937d930f93803eb576774d9`.

### inherit-candidate-verify

- Start/end: 2026-11-17T20:56:43.970053+00:00 → 2026-11-17T20:56:44.323647+00:00.
- Exit 0; 0.359 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/verify_distribution.py", ".tmp/release-readiness/20261118/inherit-candidate"]`.
- Evidence: `inherit-candidate-verify.json`, `inherit-candidate-verify.log`; log SHA-256 `b372c0325e057e1518e29a2a820339d4626acc46a5d79a1a48a6427d84eb90e1`.

### inherit-candidate-install

- Start/end: 2026-11-17T20:56:44.503186+00:00 → 2026-11-17T20:56:51.305759+00:00.
- Exit 0; 6.797 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed312\\Scripts\\python.exe", "-m", "pip", "install", "--no-deps", "--force-reinstall", ".tmp/release-readiness/20261118/inherit-candidate/syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `inherit-candidate-install.json`, `inherit-candidate-install.log`; log SHA-256 `793aff3e8bbc6ece3a3a833c08fa1005687a864ee582a07b9555bb8ee2b4df2c`.

### folio-webkit-inherit

- Start/end: 2026-11-17T20:57:58.150819+00:00 → 2026-11-17T21:00:02.601198+00:00.
- Exit 1; 124.438 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-webkit-inherit.json`, `folio-webkit-inherit.log`; log SHA-256 `4ae9e8a280b8424410d146d74bcf377084174daccb670a4df7bbf600596e19d1`.

### foreground-candidate-studio-build

- Start/end: 2026-11-17T21:01:09.466736+00:00 → 2026-11-17T21:01:17.294642+00:00.
- Exit 0; 7.828 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "build"]`.
- Evidence: `foreground-candidate-studio-build.json`, `foreground-candidate-studio-build.log`; log SHA-256 `03312d982d59e76922dee85bd0d447a7011db2b3c5ce476b72a07b27e73c3d49`.

### foreground-candidate-stage

- Start/end: 2026-11-17T21:01:17.479331+00:00 → 2026-11-17T21:01:18.454736+00:00.
- Exit 0; 0.969 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/prepare_studio.py"]`.
- Evidence: `foreground-candidate-stage.json`, `foreground-candidate-stage.log`; log SHA-256 `30967d6dd0fd2ae66d99c74eee88dda27504bb2278d4c39f612fd95e1195b1b5`.

### foreground-candidate-build

- Start/end: 2026-11-17T21:01:18.637120+00:00 → 2026-11-17T21:01:44.819492+00:00.
- Exit 0; 26.171 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/foreground-candidate"]`.
- Evidence: `foreground-candidate-build.json`, `foreground-candidate-build.log`; log SHA-256 `29586e4b98eeca3eab0e8d8a10d03fc0d63e09c0245b3315facd5cc2ee31f396`.

### documentation-branding-final

- Start/end: 2026-11-17T21:01:44.829117+00:00 → 2026-11-17T21:01:49.091056+00:00.
- Exit 0; 4.266 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_branding.py", "tests/test_ci_release_gates.py", "-q"]`.
- Evidence: `documentation-branding-final.json`, `documentation-branding-final.log`; log SHA-256 `a121c191d556c83d346da7cf3197a4e41428e0d2efc61ac860721f14079f3b11`.

### foreground-candidate-verify

- Start/end: 2026-11-17T21:01:45.037321+00:00 → 2026-11-17T21:01:45.381146+00:00.
- Exit 0; 0.344 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/verify_distribution.py", ".tmp/release-readiness/20261118/foreground-candidate"]`.
- Evidence: `foreground-candidate-verify.json`, `foreground-candidate-verify.log`; log SHA-256 `b372c0325e057e1518e29a2a820339d4626acc46a5d79a1a48a6427d84eb90e1`.

### foreground-candidate-install

- Start/end: 2026-11-17T21:01:45.580361+00:00 → 2026-11-17T21:01:52.090658+00:00.
- Exit 0; 6.516 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed312\\Scripts\\python.exe", "-m", "pip", "install", "--no-deps", "--force-reinstall", ".tmp/release-readiness/20261118/foreground-candidate/syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `foreground-candidate-install.json`, `foreground-candidate-install.log`; log SHA-256 `64796c735e7c3be7cb71a86cda06cb7ca9fe3ccb25d19aad090665fc01c76e27`.

### folio-release-webkit

- Start/end: 2026-11-17T21:02:19.051774+00:00 → 2026-11-17T21:04:28.992815+00:00.
- Exit 1; 129.937 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-release-webkit.json`, `folio-release-webkit.log`; log SHA-256 `4ae9e8a280b8424410d146d74bcf377084174daccb670a4df7bbf600596e19d1`.

### connected-repair-release

- Start/end: 2026-11-17T21:02:29.237613+00:00 → 2026-11-17T21:02:52.843024+00:00.
- Exit 0; 23.61 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "agentbus.repair.integration.acceptance", "--python", ".tmp/release-readiness/20261118/installed312/Scripts/python.exe", "--output", ".tmp/release-readiness/20261118/connected-release"]`.
- Evidence: `connected-repair-release.json`, `connected-repair-release.log`; log SHA-256 `c985ef08b4baba1ee76894bbd36d0cc08ae2311dc3c6d39f090dcb43061fbf13`.

### release-artifacts-repeat

- Start/end: 2026-11-17T21:03:02.620559+00:00 → 2026-11-17T21:03:29.947759+00:00.
- Exit 0; 27.328 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/repeat-artifacts"]`.
- Evidence: `release-artifacts-repeat.json`, `release-artifacts-repeat.log`; log SHA-256 `589497a0830a5b501ea3772fb132ee2147cb09f43d1337c4c4f186ebe662ac8a`.

### vsix-release

- Start/end: 2026-11-17T21:03:12.806310+00:00 → 2026-11-17T21:03:21.635482+00:00.
- Exit 0; 8.844 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["node", "node_modules/@vscode/vsce/vsce", "package", "--no-dependencies", "-o", "../../.tmp/release-readiness/20261118/release-artifacts/syndra-vscode-0.6.0-beta.1.vsix"]`.
- Evidence: `vsix-release.json`, `vsix-release.log`; log SHA-256 `86ba543b41597aeb481c5f4b92e0c5e100bae00b14b5abf24637226d289c9463`.

### clean-runtime-install

- Start/end: 2026-11-17T21:03:50.884859+00:00 → 2026-11-17T21:04:37.397796+00:00.
- Exit 0; 46.516 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\clean-runtime\\Scripts\\python.exe", "-m", "pip", "install", "<checkout>\\.tmp\\release-readiness\\20261118\\release-artifacts\\syndra-0.6.0b1-py3-none-any.whl[ide,mcp,repair]"]`.
- Evidence: `clean-runtime-install.json`, `clean-runtime-install.log`; log SHA-256 `ede8b16d17435ad32fe04b7a5255d33cc934b1627bd4dfa761a11c0b30f2b4e8`.

### repair-release-webkit

- Start/end: 2026-11-17T21:04:29.164602+00:00 → 2026-11-17T21:05:08.030572+00:00.
- Exit 0; 38.875 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-release-webkit.json`, `repair-release-webkit.log`; log SHA-256 `9c3dbba0b20933b020a2dfad5cc744bb98c8d600dbfece92a17d1ce280c9e29b`.

### clean-runtime-origin

- Start/end: 2026-11-17T21:04:37.584638+00:00 → 2026-11-17T21:04:38.743215+00:00.
- Exit 0; 1.156 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\clean-runtime\\Scripts\\python.exe", "-I", "-c", "import sys,syndra,agentbus.policy,agentbus.control.admission;from pathlib import Path;assert Path(syndra.__file__).resolve().is_relative_to(Path(sys.prefix).resolve());print(sys.version);print(syndra.__version__)"]`.
- Evidence: `clean-runtime-origin.json`, `clean-runtime-origin.log`; log SHA-256 `9354b6415d9d10d8535b4cc0d02371b55f235cccee681a894b5dd5c972f9b93e`.

### clean-runtime-quickstart

- Start/end: 2026-11-17T21:04:38.922277+00:00 → 2026-11-17T21:04:42.388775+00:00.
- Exit 0; 3.469 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\clean-runtime\\Scripts\\python.exe", "-I", "-m", "syndra.cli", "quickstart", "--json"]`.
- Evidence: `clean-runtime-quickstart.json`, `clean-runtime-quickstart.log`; log SHA-256 `fecf946f03b5cb3838428bcdf2c3e5234096c08cbe71a31792c6cddc4eaa2c13`.

### clean-runtime-pip-check

- Start/end: 2026-11-17T21:04:42.593739+00:00 → 2026-11-17T21:04:44.081266+00:00.
- Exit 0; 1.484 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\clean-runtime\\Scripts\\python.exe", "-m", "pip", "check"]`.
- Evidence: `clean-runtime-pip-check.json`, `clean-runtime-pip-check.log`; log SHA-256 `672aec65a45bed50e70229c70934a7eebf0462595c459b83023027645d57cccf`.

### clean-runtime-dependencies

- Start/end: 2026-11-17T21:04:44.247190+00:00 → 2026-11-17T21:04:44.908689+00:00.
- Exit 0; 0.656 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\clean-runtime\\Scripts\\python.exe", "-m", "pip", "list", "--format=json"]`.
- Evidence: `clean-runtime-dependencies.json`, `clean-runtime-dependencies.log`; log SHA-256 `27193221142851cdfc040c656e4edc4cf6f43ef696c764ae810e9d98a95370cd`.

### folio-release-chromium

- Start/end: 2026-11-17T21:05:08.192955+00:00 → 2026-11-17T21:06:41.891521+00:00.
- Exit 1; 93.704 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-release-chromium.json`, `folio-release-chromium.log`; log SHA-256 `eb5bb7afc101d9cbf111137be0e77db85820b36b6fe0206e088a9991e698b8ea`.

### controls-candidate-studio-build

- Start/end: 2026-11-17T21:05:29.086131+00:00 → 2026-11-17T21:05:36.969974+00:00.
- Exit 0; 7.891 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "build"]`.
- Evidence: `controls-candidate-studio-build.json`, `controls-candidate-studio-build.log`; log SHA-256 `fc58a9c0cf5e426c243fdb7691132bf2c291d69e8264181d9707b462abebdcad`.

### controls-candidate-stage

- Start/end: 2026-11-17T21:05:37.155982+00:00 → 2026-11-17T21:05:38.266054+00:00.
- Exit 0; 1.11 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/prepare_studio.py"]`.
- Evidence: `controls-candidate-stage.json`, `controls-candidate-stage.log`; log SHA-256 `30967d6dd0fd2ae66d99c74eee88dda27504bb2278d4c39f612fd95e1195b1b5`.

### controls-candidate-build

- Start/end: 2026-11-17T21:05:38.465117+00:00 → 2026-11-17T21:06:04.167639+00:00.
- Exit 0; 25.703 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/controls-candidate"]`.
- Evidence: `controls-candidate-build.json`, `controls-candidate-build.log`; log SHA-256 `179058a99e7cd5e44ef630a67e01a2e92701c0a2c36e5a53966510cf81ade10a`.

### controls-candidate-verify

- Start/end: 2026-11-17T21:06:04.358370+00:00 → 2026-11-17T21:06:04.688333+00:00.
- Exit 0; 0.328 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/verify_distribution.py", ".tmp/release-readiness/20261118/controls-candidate"]`.
- Evidence: `controls-candidate-verify.json`, `controls-candidate-verify.log`; log SHA-256 `b372c0325e057e1518e29a2a820339d4626acc46a5d79a1a48a6427d84eb90e1`.

### controls-candidate-install

- Start/end: 2026-11-17T21:06:04.876755+00:00 → 2026-11-17T21:06:11.046928+00:00.
- Exit 0; 6.172 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed312\\Scripts\\python.exe", "-m", "pip", "install", "--no-deps", "--force-reinstall", ".tmp/release-readiness/20261118/controls-candidate/syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `controls-candidate-install.json`, `controls-candidate-install.log`; log SHA-256 `e5bbeb36ece0d426493c748630c34d936830c8c2ade99a8a984612849943913e`.

### package-audit-foreground

- Start/end: 2026-11-17T21:06:38.925747+00:00 → 2026-11-17T21:06:41.320599+00:00.
- Exit 0; 2.39 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "agentbus.release_packaging", ".tmp/release-readiness/20261118/release-artifacts/syndra-0.6.0b1-py3-none-any.whl", ".tmp/release-readiness/20261118/release-artifacts/syndra-0.6.0b1.tar.gz", "--compare-dir", ".tmp/release-readiness/20261118/repeat-artifacts", "--json"]`.
- Evidence: `package-audit-foreground.json`, `package-audit-foreground.log`; log SHA-256 `a97ceebb767de73f911cd9b300a0828469184da9cf6e33c6d4e5926ddfb4fad5`.

### repair-release-chromium

- Start/end: 2026-11-17T21:06:42.047215+00:00 → 2026-11-17T21:07:02.568622+00:00.
- Exit 0; 20.515 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-release-chromium.json`, `repair-release-chromium.log`; log SHA-256 `9c3dbba0b20933b020a2dfad5cc744bb98c8d600dbfece92a17d1ce280c9e29b`.

### folio-release-firefox

- Start/end: 2026-11-17T21:07:02.729570+00:00 → 2026-11-17T21:08:42.871533+00:00.
- Exit 0; 100.141 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-release-firefox.json`, `folio-release-firefox.log`; log SHA-256 `7002c93fb304c678794022be73c09d42c4fee334cfe957742da51830d5b0ad96`.

### release-repeat-controls

- Start/end: 2026-11-17T21:07:07.695475+00:00 → 2026-11-17T21:07:35.714095+00:00.
- Exit 0; 28.032 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/repeat-controls"]`.
- Evidence: `release-repeat-controls.json`, `release-repeat-controls.log`; log SHA-256 `a0d79d0ea35a3f11559d998b8eeed26079730534fce4ee83c120188349d28de1`.

### folio-verified-webkit

- Start/end: 2026-11-17T21:07:17.800674+00:00 → 2026-11-17T21:09:36.627551+00:00.
- Exit 1; 138.828 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-verified-webkit.json`, `folio-verified-webkit.log`; log SHA-256 `a9d557edc05048c2f2a2f9d3be77a4d84d8fa2f9b36b4466402d30f6751d06e6`.

### clean-runtime-final-wheel

- Start/end: 2026-11-17T21:07:28.046094+00:00 → 2026-11-17T21:07:35.046958+00:00.
- Exit 0; 7.0 seconds; timeout=False.
- cwd: `.`; command: `[".tmp/release-readiness/20261118/clean-runtime/Scripts/python.exe", "-m", "pip", "install", "--no-deps", "--force-reinstall", ".tmp/release-readiness/20261118/release-artifacts/syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `clean-runtime-final-wheel.json`, `clean-runtime-final-wheel.log`; log SHA-256 `4dd05504a93d6f141ad861a88f82baec4179b2c358d407324ad8f8a4647d1a4c`.

### clean-runtime-pip-upgrade

- Start/end: 2026-11-17T21:07:59.437066+00:00 → 2026-11-17T21:08:10.044780+00:00.
- Exit 0; 10.61 seconds; timeout=False.
- cwd: `.`; command: `[".tmp/release-readiness/20261118/clean-runtime/Scripts/python.exe", "-m", "pip", "install", "--upgrade", "pip"]`.
- Evidence: `clean-runtime-pip-upgrade.json`, `clean-runtime-pip-upgrade.log`; log SHA-256 `f0722cb91abd015370834f031bf4eb60d026b96d2d8cd7e688276bf03a03c913`.

### distribution-verify-release

- Start/end: 2026-11-17T21:07:59.437066+00:00 → 2026-11-17T21:07:59.798389+00:00.
- Exit 0; 0.36 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/verify_distribution.py", ".tmp/release-readiness/20261118/release-artifacts"]`.
- Evidence: `distribution-verify-release.json`, `distribution-verify-release.log`; log SHA-256 `b372c0325e057e1518e29a2a820339d4626acc46a5d79a1a48a6427d84eb90e1`.

### vsix-audit-release

- Start/end: 2026-11-17T21:07:59.444756+00:00 → 2026-11-17T21:08:00.091323+00:00.
- Exit 0; 0.657 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["node", "scripts/audit-vsix.mjs", "../../.tmp/release-readiness/20261118/release-artifacts/syndra-vscode-0.6.0-beta.1.vsix"]`.
- Evidence: `vsix-audit-release.json`, `vsix-audit-release.log`; log SHA-256 `bb9f1d4759b4518fd13a11640905b84be701c300dda5966374e7c854e0bccd32`.

### package-audit-final

- Start/end: 2026-11-17T21:08:20.253246+00:00 → 2026-11-17T21:08:23.408014+00:00.
- Exit 0; 3.156 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "agentbus.release_packaging", ".tmp/release-readiness/20261118/release-artifacts/syndra-0.6.0b1-py3-none-any.whl", ".tmp/release-readiness/20261118/release-artifacts/syndra-0.6.0b1.tar.gz", "--compare-dir", ".tmp/release-readiness/20261118/repeat-controls", "--json"]`.
- Evidence: `package-audit-final.json`, `package-audit-final.log`; log SHA-256 `651bd6009444ec3d8316a7d37d4c3520a521332442583cd274c9a68b455d9ea9`.

### release-security-final

- Start/end: 2026-11-17T21:08:20.253246+00:00 → 2026-11-17T21:08:28.933715+00:00.
- Exit 0; 8.687 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", ".tmp/release-readiness/20261118/security_final.py"]`.
- Evidence: `release-security-final.json`, `release-security-final.log`; log SHA-256 `9342499666cefd9db6c2a3bdf13a4a6164983494075bb3a4f44f00cac5f0a0f1`.

### repair-release-firefox

- Start/end: 2026-11-17T21:08:43.035834+00:00 → 2026-11-17T21:09:15.279145+00:00.
- Exit 0; 32.25 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-release-firefox.json`, `repair-release-firefox.log`; log SHA-256 `9c3dbba0b20933b020a2dfad5cc744bb98c8d600dbfece92a17d1ce280c9e29b`.

### python-3.13-wheel-final

- Start/end: 2026-11-17T21:08:53.901829+00:00 → 2026-11-17T21:09:02.544674+00:00.
- Exit 0; 8.641 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed313\\Scripts\\python.exe", "-m", "pip", "install", "--no-deps", "--force-reinstall", "<checkout>\\.tmp\\release-readiness\\20261118\\release-artifacts\\syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `python-3.13-wheel-final.json`, `python-3.13-wheel-final.log`; log SHA-256 `4dd05504a93d6f141ad861a88f82baec4179b2c358d407324ad8f8a4647d1a4c`.

### python-3.11-wheel-final

- Start/end: 2026-11-17T21:08:53.905822+00:00 → 2026-11-17T21:09:02.595133+00:00.
- Exit 0; 8.688 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed311\\Scripts\\python.exe", "-m", "pip", "install", "--no-deps", "--force-reinstall", "<checkout>\\.tmp\\release-readiness\\20261118\\release-artifacts\\syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `python-3.11-wheel-final.json`, `python-3.11-wheel-final.log`; log SHA-256 `4dd05504a93d6f141ad861a88f82baec4179b2c358d407324ad8f8a4647d1a4c`.

### python-3.14-wheel-final

- Start/end: 2026-11-17T21:08:53.913010+00:00 → 2026-11-17T21:09:02.426381+00:00.
- Exit 0; 8.516 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed314\\Scripts\\python.exe", "-m", "pip", "install", "--no-deps", "--force-reinstall", "<checkout>\\.tmp\\release-readiness\\20261118\\release-artifacts\\syndra-0.6.0b1-py3-none-any.whl"]`.
- Evidence: `python-3.14-wheel-final.json`, `python-3.14-wheel-final.log`; log SHA-256 `f97ca29d71e0c1785b07e9693f2384aa4e0cecbfcb6b37c14560278b5368371e`.

### python-3.14-quickstart-final

- Start/end: 2026-11-17T21:09:02.693011+00:00 → 2026-11-17T21:09:07.699368+00:00.
- Exit 0; 5.016 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed314\\Scripts\\python.exe", "-I", "-m", "syndra.cli", "quickstart", "--json"]`.
- Evidence: `python-3.14-quickstart-final.json`, `python-3.14-quickstart-final.log`; log SHA-256 `50a77d98070f64303730bc6c43e43efc53b10500b0f45b08cbf4dc5a7306529f`.

### python-3.13-quickstart-final

- Start/end: 2026-11-17T21:09:02.802580+00:00 → 2026-11-17T21:09:07.592563+00:00.
- Exit 0; 4.797 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed313\\Scripts\\python.exe", "-I", "-m", "syndra.cli", "quickstart", "--json"]`.
- Evidence: `python-3.13-quickstart-final.json`, `python-3.13-quickstart-final.log`; log SHA-256 `16a24850781cf3b6930cc95ac63cd8d7d581849cb3fef450fa76f031767c327c`.

### python-3.11-quickstart-final

- Start/end: 2026-11-17T21:09:02.832075+00:00 → 2026-11-17T21:09:07.577349+00:00.
- Exit 0; 4.75 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed311\\Scripts\\python.exe", "-I", "-m", "syndra.cli", "quickstart", "--json"]`.
- Evidence: `python-3.11-quickstart-final.json`, `python-3.11-quickstart-final.log`; log SHA-256 `7e30af9fa1da98d146ef6cf9457019ea1b7d2d7854ae9cda09ae82db09aaec53`.

### python-3.11-boundaries-final

- Start/end: 2026-11-17T21:09:07.802087+00:00 → 2026-11-17T21:09:21.179617+00:00.
- Exit 0; 13.375 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed311\\Scripts\\python.exe", "-m", "pytest", "tests/test_tool_policy.py", "tests/test_control_supervisor.py", "tests/test_control_admission.py", "tests/test_mcp_stdio_transport.py", "tests/test_sandbox_process.py", "tests/test_filesystem_security.py", "tests/property/test_runtime_state_properties.py", "-q", "--basetemp", "<checkout>\\.tmp\\release-readiness\\20261118\\platform-final-3.11-tmp"]`.
- Evidence: `python-3.11-boundaries-final.json`, `python-3.11-boundaries-final.log`; log SHA-256 `624bd40feb713aca06206dad38191d1cc9813c2d3543fb6c8179e4b13f07bef2`.

### python-3.13-boundaries-final

- Start/end: 2026-11-17T21:09:07.810559+00:00 → 2026-11-17T21:09:21.192114+00:00.
- Exit 0; 13.375 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed313\\Scripts\\python.exe", "-m", "pytest", "tests/test_tool_policy.py", "tests/test_control_supervisor.py", "tests/test_control_admission.py", "tests/test_mcp_stdio_transport.py", "tests/test_sandbox_process.py", "tests/test_filesystem_security.py", "tests/property/test_runtime_state_properties.py", "-q", "--basetemp", "<checkout>\\.tmp\\release-readiness\\20261118\\platform-final-3.13-tmp"]`.
- Evidence: `python-3.13-boundaries-final.json`, `python-3.13-boundaries-final.log`; log SHA-256 `7f17986578863305c9f6b32621b4dd22b847096cde7b26a2cbd51f7a6c3c7715`.

### python-3.14-boundaries-final

- Start/end: 2026-11-17T21:09:07.921652+00:00 → 2026-11-17T21:09:21.295334+00:00.
- Exit 0; 13.375 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\installed314\\Scripts\\python.exe", "-m", "pytest", "tests/test_tool_policy.py", "tests/test_control_supervisor.py", "tests/test_control_admission.py", "tests/test_mcp_stdio_transport.py", "tests/test_sandbox_process.py", "tests/test_filesystem_security.py", "tests/property/test_runtime_state_properties.py", "-q", "--basetemp", "<checkout>\\.tmp\\release-readiness\\20261118\\platform-final-3.14-tmp"]`.
- Evidence: `python-3.14-boundaries-final.json`, `python-3.14-boundaries-final.log`; log SHA-256 `7db51045f4b24788222e2070c5fd75add2152599be3f8f29bcce2d9b60778f35`.

### folio-release-msedge

- Start/end: 2026-11-17T21:09:15.493263+00:00 → 2026-11-17T21:10:36.341653+00:00.
- Exit 0; 80.86 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-release-msedge.json`, `folio-release-msedge.log`; log SHA-256 `500918076b7e1600a112e133a3c6b05fd6eb6188fecaeff2adde46b26ec234fc`.

### repair-release-msedge

- Start/end: 2026-11-17T21:10:36.494229+00:00 → 2026-11-17T21:10:55.358808+00:00.
- Exit 0; 18.875 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-release-msedge.json`, `repair-release-msedge.log`; log SHA-256 `9c3dbba0b20933b020a2dfad5cc744bb98c8d600dbfece92a17d1ce280c9e29b`.

### mcp-observer-regression

- Start/end: 2026-11-17T21:12:04.827054+00:00 → 2026-11-17T21:12:10.728258+00:00.
- Exit 0; 5.891 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_mcp_adversarial.py", "tests/test_mcp_stdio_transport.py", "tests/test_sandbox_process_stress.py", "-q"]`.
- Evidence: `mcp-observer-regression.json`, `mcp-observer-regression.log`; log SHA-256 `17ca7f26aa5db9bd8dea804d03759d4e989d75e3c0ab590b78d7e456b9292b9a`.

### folio-recheck-webkit

- Start/end: 2026-11-17T21:12:11.193310+00:00 → 2026-11-17T21:14:22.629786+00:00.
- Exit 0; 131.438 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-recheck-webkit.json`, `folio-recheck-webkit.log`; log SHA-256 `7a1ba7cc90ee9c33ccb63c21889d94e4917bb66efb76783c01ef608fd57d1594`.

### pytest-release-final

- Start/end: 2026-11-17T21:12:58.232781+00:00 → 2026-11-17T21:32:10.787036+00:00.
- Exit 0; 1152.547 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "-q", "--basetemp", ".tmp/release-readiness/20261118/full-release-tmp", "--junitxml", ".tmp/release-readiness/20261118/full-release.xml", "--durations=20"]`.
- Evidence: `pytest-release-final.json`, `pytest-release-final.log`; log SHA-256 `d54486f4e3c148250fdce914ed6067bc4c4beb09501e5e5de10d66f61c777621`.

### cross-client-electron-release

- Start/end: 2026-11-17T21:13:08.479900+00:00 → 2026-11-17T21:15:01.428052+00:00.
- Exit 0; 112.938 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "run", "test:integration"]`.
- Evidence: `cross-client-electron-release.json`, `cross-client-electron-release.log`; log SHA-256 `2cb0d234e1fb6d561f4051f1704da1cd9ca7a72c489e448659d79101b9858a08`.

### final-sdist-build

- Start/end: 2026-11-17T21:13:18.754388+00:00 → 2026-11-17T21:13:32.084966+00:00.
- Exit 0; 13.328 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--sdist", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/final-sdist"]`.
- Evidence: `final-sdist-build.json`, `final-sdist-build.log`; log SHA-256 `e0f749757b5781c750d4d51f3f9ec9e2013ebaaa0ff4de918c772fac4198a93f`.

### soak-write-diagnostic

- Start/end: 2026-11-17T21:14:56.071520+00:00 → 2026-11-17T21:17:01.856436+00:00.
- Exit 0; 125.797 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", ".tmp/release-readiness/20261118/soak_diagnostic.py"]`.
- Evidence: `soak-write-diagnostic.json`, `soak-write-diagnostic.log`; log SHA-256 `04932c71ebfd3a5d23db1c3e759ad52923da8311d6349f78a940eede133e244c`.

### repair-verified-webkit

- Start/end: 2026-11-17T21:14:56.104903+00:00 → 2026-11-17T21:15:41.777158+00:00.
- Exit 0; 45.672 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/customer-repair-smoke.mjs"]`.
- Evidence: `repair-verified-webkit.json`, `repair-verified-webkit.log`; log SHA-256 `9c3dbba0b20933b020a2dfad5cc744bb98c8d600dbfece92a17d1ce280c9e29b`.

### folio-verified-chromium

- Start/end: 2026-11-17T21:14:56.110346+00:00 → 2026-11-17T21:16:19.895927+00:00.
- Exit 0; 83.782 seconds; timeout=False.
- cwd: `.`; command: `["node", "studio/scripts/folio-smoke.mjs"]`.
- Evidence: `folio-verified-chromium.json`, `folio-verified-chromium.log`; log SHA-256 `15383df5bf15a8faae25fda37147049d105a5d8ae36c632810820bc084f03421`.

### final-sdist-repeat

- Start/end: 2026-11-17T21:16:25.722029+00:00 → 2026-11-17T21:16:37.389936+00:00.
- Exit 0; 11.672 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--sdist", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/final-sdist-repeat"]`.
- Evidence: `final-sdist-repeat.json`, `final-sdist-repeat.log`; log SHA-256 `e0f749757b5781c750d4d51f3f9ec9e2013ebaaa0ff4de918c772fac4198a93f`.

### clean-runtime-quickstart-final

- Start/end: 2026-11-17T21:16:35.858446+00:00 → 2026-11-17T21:16:39.444059+00:00.
- Exit 0; 3.578 seconds; timeout=False.
- cwd: `.`; command: `[".tmp/release-readiness/20261118/clean-runtime/Scripts/python.exe", "-I", "-m", "syndra.cli", "quickstart", "--json"]`.
- Evidence: `clean-runtime-quickstart-final.json`, `clean-runtime-quickstart-final.log`; log SHA-256 `505643cd35d3e8585bf664191e40ecc6bf860398ccac492d0b3a7dad1f878566`.

### clean-runtime-audit

- Start/end: 2026-11-17T21:16:39.930213+00:00 → 2026-11-17T21:16:46.976517+00:00.
- Exit 0; 7.047 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pip_audit", "--path", ".tmp/release-readiness/20261118/clean-runtime/Lib/site-packages", "--format=json"]`.
- Evidence: `clean-runtime-audit.json`, `clean-runtime-audit.log`; log SHA-256 `374ef1073df5f99ad03777cedd90f3dbefe2ab8b0390056d9153521dc2295d99`.

### final-wheel-repeat

- Start/end: 2026-11-17T21:19:44.626338+00:00 → 2026-11-17T21:19:55.726065+00:00.
- Exit 0; 11.094 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--wheel", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/complete-repeat"]`.
- Evidence: `final-wheel-repeat.json`, `final-wheel-repeat.log`; log SHA-256 `7e84bac97ab72418158b9c85d101cfdfc47b5816e5585462c8fce59e5a7cd8c4`.

### documentation-sdist-build

- Start/end: 2026-11-17T21:22:43.807825+00:00 → 2026-11-17T21:22:54.234765+00:00.
- Exit 0; 10.422 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--sdist", "--no-isolation", "--outdir", "<checkout>\\.tmp\\release-readiness\\20261118\\documentation-sdist-build"]`.
- Evidence: `documentation-sdist-build.json`, `documentation-sdist-build.log`; log SHA-256 `e0f749757b5781c750d4d51f3f9ec9e2013ebaaa0ff4de918c772fac4198a93f`.

### documentation-sdist-repeat

- Start/end: 2026-11-17T21:22:54.440954+00:00 → 2026-11-17T21:23:03.970791+00:00.
- Exit 0; 9.532 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--sdist", "--no-isolation", "--outdir", "<checkout>\\.tmp\\release-readiness\\20261118\\documentation-sdist-repeat"]`.
- Evidence: `documentation-sdist-repeat.json`, `documentation-sdist-repeat.log`; log SHA-256 `e0f749757b5781c750d4d51f3f9ec9e2013ebaaa0ff4de918c772fac4198a93f`.

### distribution-verify-complete

- Start/end: 2026-11-17T21:23:04.192554+00:00 → 2026-11-17T21:23:04.524166+00:00.
- Exit 0; 0.329 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/verify_distribution.py", "<checkout>\\.tmp\\release-readiness\\20261118\\release-artifacts"]`.
- Evidence: `distribution-verify-complete.json`, `distribution-verify-complete.log`; log SHA-256 `b372c0325e057e1518e29a2a820339d4626acc46a5d79a1a48a6427d84eb90e1`.

### package-audit-complete

- Start/end: 2026-11-17T21:23:04.741109+00:00 → 2026-11-17T21:23:07.247979+00:00.
- Exit 1; 2.516 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "agentbus.release_packaging", "<checkout>\\.tmp\\release-readiness\\20261118\\release-artifacts\\syndra-0.6.0b1-py3-none-any.whl", "<checkout>\\.tmp\\release-readiness\\20261118\\release-artifacts\\syndra-0.6.0b1.tar.gz", "--compare-dir", "<checkout>\\.tmp\\release-readiness\\20261118\\complete-repeat", "--json"]`.
- Evidence: `package-audit-complete.json`, `package-audit-complete.log`; log SHA-256 `c26cfe9a6b972f44e891b0f1d09f38a7c9b6d5822f9c13e48042615e32b1d835`.

### control-release-final

- Start/end: 2026-11-17T21:23:24.476030+00:00 → 2026-11-17T21:25:31.056247+00:00.
- Exit 0; 126.578 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "syndra.control.acceptance"]`.
- Evidence: `control-release-final.json`, `control-release-final.log`; log SHA-256 `56546256bb2a9bc6940e474d8272ca586fe590e5be7c7452b795e88401155de4`.

### artifact-source-equivalence

- Start/end: 2026-11-17T21:25:33.172625+00:00 → 2026-11-17T21:25:34.683255+00:00.
- Exit 0; 1.515 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", ".tmp/release-readiness/20261118/artifact_equivalence.py"]`.
- Evidence: `artifact-source-equivalence.json`, `artifact-source-equivalence.log`; log SHA-256 `81d549677a533b66f4c2987ea5f33a88fa259159adae7a33b9078478fe2b12aa`.

### isolated-repeat-build

- Start/end: 2026-11-17T21:27:01.886292+00:00 → 2026-11-17T21:27:26.700140+00:00.
- Exit 0; 24.812 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--no-isolation", "--outdir", ".tmp/release-readiness/20261118/isolated-repeat"]`.
- Evidence: `isolated-repeat-build.json`, `isolated-repeat-build.log`; log SHA-256 `c1ee2020330bf44d9f08c13799d368fdfad213584e873f61697d656e10e1a2f9`.

### release-security-complete

- Start/end: 2026-11-17T21:27:12.074267+00:00 → 2026-11-17T21:27:19.465939+00:00.
- Exit 0; 7.391 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", ".tmp/release-readiness/20261118/security_final.py"]`.
- Evidence: `release-security-complete.json`, `release-security-complete.log`; log SHA-256 `b8364f7c8c632b5746a037d1f9430d6a97bd3a7bace630f6c9f6695e0b4f8eff`.

### package-audit-isolated

- Start/end: 2026-11-17T21:28:33.226050+00:00 → 2026-11-17T21:28:35.722800+00:00.
- Exit 0; 2.5 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "agentbus.release_packaging", ".tmp/release-readiness/20261118/release-artifacts/syndra-0.6.0b1-py3-none-any.whl", ".tmp/release-readiness/20261118/release-artifacts/syndra-0.6.0b1.tar.gz", "--compare-dir", ".tmp/release-readiness/20261118/isolated-repeat", "--json"]`.
- Evidence: `package-audit-isolated.json`, `package-audit-isolated.log`; log SHA-256 `1bff0ff6f16a379ebd41edff31c9e6dd5bed97f8cc69c9f75f714196bf7eda98`.

### environment-inventory

- Start/end: 2026-11-17T21:30:58.648283+00:00 → 2026-11-17T21:31:03.621092+00:00.
- Exit 0; 4.984 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", ".tmp/release-readiness/20261118/inventory_environments.py"]`.
- Evidence: `environment-inventory.json`, `environment-inventory.log`; log SHA-256 `33e6ee51ccf36c4306726898800ada3dfac345db217f5ebce57e1a0558e9a0cc`.

### branch-protection-final-read

- Start/end: 2026-11-17T21:31:21.314239+00:00 → 2026-11-17T21:31:22.119852+00:00.
- Exit 0; 0.813 seconds; timeout=False.
- cwd: `.`; command: `["gh", "api", "repos/<canonical-repository>/branches/main/protection", "--jq", "{required_status_checks: .required_status_checks, required_reviews: .required_pull_request_reviews.required_approving_review_count, enforce_admins: .enforce_admins.enabled, allow_force_pushes: .allow_force_pushes.enabled, allow_deletions: .allow_deletions.enabled, conversation_resolution: .required_conversation_resolution.enabled}"]`.
- Evidence: `branch-protection-final-read.json`, `branch-protection-final-read.log`; log SHA-256 `1d6fcbf5125096ad95859cf3666624d2a329e27f21fd322ef6d21e60994e6df3`.

### final-documentation-check

- Start/end: 2026-11-18T08:14:06.513017+00:00 → 2026-11-18T08:14:17.621180+00:00.
- Exit 0; 11.109 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pytest", "tests/test_branding.py", "tests/test_ci_release_gates.py", "-q"]`.
- Evidence: `final-documentation-check.json`, `final-documentation-check.log`; log SHA-256 `1f8de2fc43b01973bb1aae755bdf2d474d839b73af8e796ca18a131cb21dfa36`.

### vscode-audit-resumed

- Start/end: 2026-11-18T08:17:02.694934+00:00 → 2026-11-18T08:17:11.290375+00:00.
- Exit 1; 8.594 seconds; timeout=False.
- cwd: `extensions/vscode`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "audit", "--json"]`.
- Evidence: `vscode-audit-resumed.json`, `vscode-audit-resumed.log`; log SHA-256 `d0681290027367c6f9446f3577be5a42c38a2367de64a20fb270fab28bbbf6bc`.

### python-audit-resumed

- Start/end: 2026-11-18T08:17:02.696935+00:00 → 2026-11-18T08:17:06.518109+00:00.
- Exit 0; 3.813 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "pip_audit", "--path", ".tmp/release-readiness/20261118/clean-runtime/Lib/site-packages", "--format=json"]`.
- Evidence: `python-audit-resumed.json`, `python-audit-resumed.log`; log SHA-256 `374ef1073df5f99ad03777cedd90f3dbefe2ab8b0390056d9153521dc2295d99`.

### studio-audit-resumed

- Start/end: 2026-11-18T08:17:02.696935+00:00 → 2026-11-18T08:17:10.958910+00:00.
- Exit 1; 8.266 seconds; timeout=False.
- cwd: `studio`; command: `["C:\\Program Files\\nodejs\\npm.cmd", "audit", "--json"]`.
- Evidence: `studio-audit-resumed.json`, `studio-audit-resumed.log`; log SHA-256 `ea1de111e5560891c4445e28dd29b1c8a5589b40ddd5f7462448e395fd8246de`.

### release-report-final-review

- Start/end: 2026-11-18T08:19:20.873243+00:00 → 2026-11-18T08:19:21.234847+00:00.
- Exit 1; 0.359 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", ".tmp/release-readiness/20261118/final_review.py"]`.
- Evidence: `release-report-final-review.json`, `release-report-final-review.log`; log SHA-256 `f14775de1a6d438cb5a30f880e4e95096a1248db529206eacd7b6171d0a20f3e`.

### release-report-review-fixed

- Start/end: 2026-11-18T08:19:33.780210+00:00 → 2026-11-18T08:19:36.822154+00:00.
- Exit 1; 3.047 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", ".tmp/release-readiness/20261118/final_review.py"]`.
- Evidence: `release-report-review-fixed.json`, `release-report-review-fixed.log`; log SHA-256 `fa919b438b9b3fe162d07379e2ed475c7876b3a1948059126f98cd85252233d8`.

### sdist-resumed-build

- Start/end: 2026-11-18T08:21:37.341944+00:00 → 2026-11-18T08:21:50.201271+00:00.
- Exit 0; 12.859 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--sdist", "--no-isolation", "--outdir", "<checkout>\\.tmp\\release-readiness\\20261118\\sdist-resumed-build"]`.
- Evidence: `sdist-resumed-build.json`, `sdist-resumed-build.log`; log SHA-256 `e0f749757b5781c750d4d51f3f9ec9e2013ebaaa0ff4de918c772fac4198a93f`.

### sdist-resumed-repeat

- Start/end: 2026-11-18T08:21:50.481942+00:00 → 2026-11-18T08:21:59.924149+00:00.
- Exit 0; 9.438 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "build", "--sdist", "--no-isolation", "--outdir", "<checkout>\\.tmp\\release-readiness\\20261118\\sdist-resumed-repeat"]`.
- Evidence: `sdist-resumed-repeat.json`, `sdist-resumed-repeat.log`; log SHA-256 `e0f749757b5781c750d4d51f3f9ec9e2013ebaaa0ff4de918c772fac4198a93f`.

### distribution-verify-resumed

- Start/end: 2026-11-18T08:22:00.152316+00:00 → 2026-11-18T08:22:00.513732+00:00.
- Exit 0; 0.36 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "scripts/verify_distribution.py", "<checkout>\\.tmp\\release-readiness\\20261118\\release-artifacts"]`.
- Evidence: `distribution-verify-resumed.json`, `distribution-verify-resumed.log`; log SHA-256 `b372c0325e057e1518e29a2a820339d4626acc46a5d79a1a48a6427d84eb90e1`.

### package-audit-resumed

- Start/end: 2026-11-18T08:22:00.748555+00:00 → 2026-11-18T08:22:03.180471+00:00.
- Exit 0; 2.438 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "-m", "agentbus.release_packaging", "<checkout>\\.tmp\\release-readiness\\20261118\\release-artifacts\\syndra-0.6.0b1-py3-none-any.whl", "<checkout>\\.tmp\\release-readiness\\20261118\\release-artifacts\\syndra-0.6.0b1.tar.gz", "--compare-dir", "<checkout>\\.tmp\\release-readiness\\20261118\\isolated-repeat", "--json"]`.
- Evidence: `package-audit-resumed.json`, `package-audit-resumed.log`; log SHA-256 `2f46ce33f359958f654227e8d7f9eb0c6dc004f8bc4e6fca2cf01a109c8aced3`.

### artifact-source-equivalence-resumed

- Start/end: 2026-11-18T08:22:03.388478+00:00 → 2026-11-18T08:22:04.830177+00:00.
- Exit 0; 1.437 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "<checkout>\\.tmp\\release-readiness\\20261118\\artifact_equivalence.py"]`.
- Evidence: `artifact-source-equivalence-resumed.json`, `artifact-source-equivalence-resumed.log`; log SHA-256 `81d549677a533b66f4c2987ea5f33a88fa259159adae7a33b9078478fe2b12aa`.

### security-resumed

- Start/end: 2026-11-18T08:22:05.050804+00:00 → 2026-11-18T08:22:11.774550+00:00.
- Exit 0; 6.734 seconds; timeout=False.
- cwd: `.`; command: `["<checkout>\\.tmp\\release-readiness\\20261118\\venv312\\Scripts\\python.exe", "<checkout>\\.tmp\\release-readiness\\20261118\\security_final.py"]`.
- Evidence: `security-resumed.json`, `security-resumed.log`; log SHA-256 `022e70f0b314eb0c718a254e09ff541a41ac3ba9ed5761b4bb14810cdbf152a1`.
