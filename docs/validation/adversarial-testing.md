# Adversarial defensive testing

AgentBus adversarial validation is local defensive testing. It attempts to
falsify containment, parsing, protocol, lifecycle, and privacy invariants using
AgentBus-owned fixtures. It does not scan or attack third-party systems.

## Fixture methodology

Repository fixtures are generated in a unique empty temporary directory with
an ownership marker. Generation refuses a non-empty destination, and cleanup
is limited to marker-owned temporary state. Platform-dependent fixtures record
which features were unavailable instead of silently pretending they ran.

The hostile repository includes:

- truncated Python and malformed TypeScript, Java, Go, and manifest files;
- Unicode filenames, binary content, and malformed ignore data;
- protected `.env`, SSH-key-shaped, token-shaped, and password-shaped files;
- generated, vendored, dependency, and nested Git metadata boundaries;
- unsafe ownership and submodule-like path text;
- a file above the default 10,000,000-byte discovery limit;
- symlink loops and broken links where the platform permits their creation.

Parser fuzz tests generate bounded malformed inputs for Python, TypeScript,
Java, and Go. Protocol fuzz tests generate malformed REST, JSON-RPC, tool,
capability, approval, budget, artifact, and cancellation documents. Property
tests check path containment, identity stability, configuration precedence,
approval binding, idempotency, migrations, trace archives, and checkpoints.
Fixed seeds and minimized regression examples keep failures reproducible.

Hostile MCP tests use either an in-memory transport or a synthetic local child
process. They exercise hangs, malformed messages, unsupported versions,
mismatched IDs, duplicate or oversized declarations, oversized output,
secret-shaped stderr, ignored cancellation, and attempted capability widening.
No public MCP server, socket outside loopback, or third-party target is used.

## Defensive scorecard

Run the release boundary scorecard locally:

```console
python -m agentbus.release_security
```

The scorecard evaluates nine explicit boundaries:

1. Filesystem containment.
2. Approval and capability scope.
3. Git safety.
4. Malformed protocol handling.
5. Synthetic hostile MCP behavior.
6. Trace and archive integrity.
7. Diagnostic privacy.
8. Python package contents.
9. VSIX contents.

Every boundary reports tested observations and unresolved limitations. Missing
real wheel, source-distribution, or VSIX bytes produce a visible warning rather
than synthetic evidence being presented as a real artifact audit. Any failed
boundary fails the release-security command.

## Containment and side effects

All default defensive probes use local temporary roots, generated repositories,
in-memory peers, loopback-only control services, or explicitly selected local
artifacts. Subprocess calls use argument arrays with `shell=False`. Provider
credentials are removed from child environments, and no live model provider is
required.

Tests may intentionally create files, SQLite databases, Git repositories,
worktrees, processes, package archives, traces, and support bundles inside
their owned roots. The tests verify cleanup and preserve ambiguous user state
rather than resetting, cleaning, or deleting it. Normal failed AgentBus runs
also do not automatically roll back source edits.

## Platforms and limits

Windows and Linux security-boundary jobs are mandatory. POSIX symlink cases run
only where POSIX semantics are available; Windows device, drive-relative,
alternate-stream, long-path, locked-file, and process-tree cases run on
Windows. A skip records an inapplicable platform case, not a cross-platform
pass.

Inputs are bounded by parser and discovery source limits, protocol model
limits, subprocess timeouts, retained-output budgets, MCP message/output
limits, and test-specific Hypothesis settings. Stress fixtures use deterministic
local children and bounded deadlines. Resource exhaustion outside those bounds
is not attempted.

## What this does not prove

Passing the scorecard is not formal penetration-test certification. It does not
prove:

- absence of exploitable vulnerabilities or supply-chain compromise;
- kernel, container, account, or network isolation;
- safety of generated code or arbitrary repository-controlled commands;
- safety, identity, or correctness of a real third-party MCP server;
- confidentiality of data sent to a live model provider;
- resistance to privileged local attackers or a compromised host;
- correctness of every filesystem, Git, SQLite, parser, or process race;
- automatic rollback of completed filesystem or external side effects.

Independent review, deployment-specific threat modeling, least-privilege OS
controls, secret management, network policy, and human diff review remain
required.
