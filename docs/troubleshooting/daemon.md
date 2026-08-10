# Daemon troubleshooting

Inspect the registry and compatibility before starting another process:

```console
agentbus daemon status --json
agentbus daemon registry --json
agentbus daemon cleanup-stale --json
```

Start or restart explicitly:

```console
agentbus daemon start --json
agentbus daemon restart --json
```

The daemon binds to numeric loopback, uses an ephemeral port by default, and
requires bearer authentication. Registry entries do not contain bearer tokens.
The VS Code extension refuses remote, credentialed, incompatible, stale, or
malformed entries.

Read bounded logs without exposing raw prompts or provider responses:

```console
agentbus daemon logs --tail 100 --json
agentbus logs --tail 100 --json
```

Stop with `agentbus daemon stop <daemon-id> --json`. If shutdown fails, do not
delete registry or process files blindly; rerun `cleanup-stale` and inspect the
reported process identity. See [Daemon Security](../daemon-security.md).
