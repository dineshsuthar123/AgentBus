# Replay completed and failed runs

Inspect and verify evidence before replaying:

```console
agentbus trace inspect <run-id> --json
agentbus trace verify <run-id> --json
agentbus replay <run-id> --mode offline --json
```

Offline replay substitutes captured provider and MCP envelopes, never contacts
Azure or Ollama, and never mutates the source repository. Strict mode requires
fully replayable evidence. Verify and simulate modes have different guarantees;
the result reports partial or non-replayable behavior instead of claiming exact
reproduction.

Replay from a checkpoint or create an isolated fork without changing the
original run:

```console
agentbus replay <run-id> --mode offline --from pre-verifier --json
agentbus replay <run-id> --mode offline --fork --change task_text='"Updated offline task"' --json
agentbus compare <left-run-id> <right-run-id> --json
```

Do not use `--live-provider-consent` unless a live route is intentional and its
cost and data handling have been reviewed.

See [Deterministic Replay](../deterministic-replay.md),
[Execution Tracing](../execution-tracing.md), and
[Run Provenance](../run-provenance.md).
