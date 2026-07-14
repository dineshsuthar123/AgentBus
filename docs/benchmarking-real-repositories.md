# Benchmarking real repositories

Real-repository benchmarks are opt-in acceptance tests, not normal unit tests.
The built-in manifest is `agentbus/evaluation/real_repositories.json`. Each
entry records a credential-free HTTPS Git URL, exact 40-character commit SHA,
SPDX license, explicit license review, bounded task, test command, optional
reviewed setup argument array, assertions, resource limits, platforms, and
tags.

The initial benchmark pins Pallets Click at
`934813e4d421071a1b3db3973c02fe2721359a6e`. Its manifest records the official
BSD-3-Clause license source. Mutable branches and tags are not accepted as
commit identifiers.

## Safety flow

1. The user selects `real-repos`, a live variant, `--live`, and
   `--allow-repository-download` explicitly.
2. AgentBus creates a marker-owned temporary session and clones with
   `shell=False`, disabled terminal prompting, and a bounded timeout.
3. It verifies `origin` and the exact checked-out commit.
4. The immutable source working tree is copied without its `.git` directory to
   a fresh disposable repository; remotes and hooks are not inherited, and
   agents never edit the source clone directly.
5. Durable execution uses AgentBus worktrees. Push and PR creation are disabled.
6. Request, token, retry, and wall-clock limits are capped by the manifest.
7. Source clones are removed only after ownership-marker validation. Failed
   execution fixtures are retained only with `--preserve-fixtures`.

Git subprocesses ignore system/global configuration, use an AgentBus-owned
empty hooks directory, disable terminal prompting, and receive a sanitized
environment without common credential-bearing variables. Verifier commands use
the same credential filtering. These controls reduce exposure but are not a
general network or process sandbox.

Setup commands are never run automatically in this alpha. The schema rejects
shell interpreters and requires an explicit review marker for any future setup
argument array. Even reviewed build hooks are untrusted, so enablement requires
an additional product/security decision.

## Command

```powershell
agentbus-eval run --suite real-repos --variant durable-azure --repeat 3 --live --allow-repository-download --max-requests 16 --max-tokens 4000 --timeout-seconds 300
```

This command performs real Git and provider network access and may incur cost.
Do not run it in ordinary CI or against credentials with broad privileges.
Small repeats provide descriptive sample statistics only, not statistical
significance.

To add a repository, verify its license at the pinned commit, review the task
and tests, use a permissive SPDX identifier (`MIT`, `Apache-2.0`,
`BSD-2-Clause`, or `BSD-3-Clause`), and add manifest-validation tests. Do not
vendor the repository.
