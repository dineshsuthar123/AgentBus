# Release-candidate acceptance

The v0.7 release-candidate acceptance is one entirely local, non-publishing
workflow. It builds AgentBus from the current checkout, installs the wheel in a
fresh virtual environment, exercises the installed product, and cleans only
AgentBus-owned temporary state.

```console
python -m agentbus.rc_acceptance
python -m agentbus.rc_acceptance --json
```

No live model provider, public repository, third-party MCP server, external
security target, package index, or publication service is contacted by the
acceptance workflow. The child environment removes common credential-bearing
variables and blocks package/network fallback. Package installation uses the
locally built wheel and a local dependency bridge.

## Required gates

The gates run in this order and stop on the first failure:

1. Build wheel and source distribution.
2. Audit package contents.
3. Create a clean environment and install the wheel.
4. Verify state and index migration targets.
5. Run noninteractive deterministic setup.
6. Run doctor against the configured workspace.
7. Complete the deterministic reviewed quickstart.
8. Build the repository index without providers.
9. Execute managed read, write, and test tools plus a reviewed durable task.
10. Request, approve, persist, and resume one exact scoped tool invocation.
11. Exercise intentional durable cancellation.
12. Restart the exact owned daemon and retire its previous process.
13. Replay the durable run offline with zero provider calls.
14. Verify sealed trace provenance and content-addressed objects.
15. Reject a synthetic hostile in-memory MCP peer.
16. Reject adversarial local filesystem and Git path forms.
17. Inspect a bounded support bundle for private-marker leakage.
18. Require reliability smoke with no owned process or worktree leaks.
19. Compare all 11 performance metrics with no broad regression.
20. Stop the daemon and remove validated AgentBus-owned runtime state.
21. Uninstall AgentBus from the fresh environment.
22. Verify post-uninstall process, worktree, registry, and credential cleanup.

Structured output records each completed gate, status, bounded detail, duration,
overall error, deterministic provider/network counts, package-audit status, and
an external-security-target count of zero. It does not expose prompts, API keys,
private marker values, or temporary source paths.

## Failure and cleanup semantics

A failed gate makes the command return nonzero and prevents later gates from
running. Failure cleanup attempts to stop only the daemon identified by the
owned registry. It never runs `git reset`, `git clean`, or destructive source
rollback.

The acceptance workspace itself is disposable and removed by its temporary
directory owner. That does not change normal AgentBus semantics: files written
to a user workspace during a failed run remain for inspection, and reports must
list created or modified artifacts. Cleanup recommendations are advisory unless
the operator explicitly confirms an AgentBus ownership-aware cleanup command.

## Evidence scope

RC acceptance combines synthetic and real local product evidence:

- synthetic fixtures drive quickstart, reliability, hostile MCP, path, and
  performance scenarios;
- the current local AgentBus checkout supplies package source and real wheel
  and source-distribution bytes to package and defensive artifact audits;
- no public repository is downloaded and no live provider is invoked.

The required CI matrix runs RC acceptance on Ubuntu with CPython 3.12. Product
acceptance runs on Ubuntu and Windows, and core tests cover CPython 3.11 through
3.14. The local milestone acceptance was also executed on Windows AMD64 with
CPython 3.14. A configured CI job is not evidence of success until it completes.

## Known gaps

Passing RC acceptance does not prove production readiness by itself. It does
not replace:

- independent security review and deployment threat modeling;
- a full ten-minute release-candidate soak;
- manual 10,000-file and 50,000-file repository scale runs;
- authorized real-repository validation for the intended customer workload;
- live-provider compatibility, availability, quality, privacy, or cost checks;
- human review of release notes, package metadata, and generated diffs;
- operating-system, container, network, identity, or secret-management policy.

Review [adversarial testing](adversarial-testing.md),
[reliability](reliability.md), [performance](performance.md), and
[real repository validation](real-repositories.md) alongside the machine-
readable acceptance report. Record candidate evidence in the
[v0.7 RC checklist](../release/v0.7-rc-checklist.md).
