## Summary

Describe the user-visible behavior and why this change is needed.

## Safety and compatibility

- Workspace, Git, tool, approval, provider, MCP, replay, or cleanup impact:
- CLI, config, protocol, schema, migration, or extension compatibility impact:
- Files created or modified after failed runs:

## Verification

List focused and full commands with results. Normal verification must remain
offline and must not require Azure, Ollama, or a public MCP server.

## Checklist

- [ ] Changes are focused and covered by success, failure, and recovery tests.
- [ ] Workspace boundaries, `shell=False`, approval binding, final review,
      redaction, and explicit live-provider consent remain intact.
- [ ] Generated protocol or migration artifacts are updated when required.
- [ ] Documentation and examples match current CLI behavior.
- [ ] No credentials, `.env`, private source, prompts, provider responses,
      databases, indexes, traces, logs, support bundles, worktrees, VSIX files,
      dependencies, caches, or personal absolute paths are included.
- [ ] `pytest`, `compileall`, relevant offline acceptance, and
      `git diff --check` pass, or any gap is explained.
- [ ] No automatic destructive rollback, push, merge, tag, publish, or release
      behavior was introduced.
