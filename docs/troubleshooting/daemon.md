# Daemon troubleshooting

Inspect the registry and compatibility before starting another process:

```console
syndra daemon status --json
syndra daemon registry --json
syndra daemon cleanup-stale --json
```

Start or restart explicitly:

```console
syndra daemon start --json
syndra daemon restart --json
```

The daemon binds to numeric loopback, uses an ephemeral port by default, and
requires bearer authentication. Registry entries do not contain bearer tokens.
The VS Code extension refuses remote, credentialed, incompatible, stale, or
malformed entries.

Read bounded logs without exposing raw prompts or provider responses:

```console
syndra daemon logs --tail 100 --json
syndra logs --tail 100 --json
```

Stop with `syndra daemon stop <daemon-id> --json`. If shutdown fails, do not
delete registry or process files blindly; rerun `cleanup-stale` and inspect the
reported process identity. See [Daemon Security](../daemon-security.md).
