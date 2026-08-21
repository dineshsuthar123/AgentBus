# AgentBus documentation

AgentBus is a local, safety-oriented software-engineering agent runtime. Start
with the deterministic provider, which follows the real planner, tool,
verification, review, persistence, and replay paths without network access.

## Start here

- [Install AgentBus](getting-started/install.md)
- [Complete the five-minute quickstart](getting-started/quickstart.md)
- [Use the VS Code extension](getting-started/vscode.md)

## Guides

- [Choose a provider](guides/providers.md)
- [Index and query a repository](guides/repository-intelligence.md)
- [Understand tools and approvals](guides/tools-and-approvals.md)
- [Replay and compare runs](guides/replay.md)
- [Connect a local MCP server](guides/mcp.md)
- [Follow practical workflows](guides/workflows.md)
- [Reproduce performance measurements](guides/performance.md)

## Reference

- [Configuration](reference/configuration.md)
- [CLI](reference/cli.md)
- [Environment variables](reference/environment-variables.md)
- [Local storage](reference/storage.md)
- [Pre-v1 compatibility](reference/compatibility.md)

## Validation

- [Real local repositories](validation/real-repositories.md)
- [Adversarial defensive testing](validation/adversarial-testing.md)
- [Reliability validation](validation/reliability.md)
- [Performance validation](validation/performance.md)
- [Release-candidate acceptance](validation/release-candidate.md)

## Release

- [v0.7 release-candidate checklist](release/v0.7-rc-checklist.md)

## Troubleshooting

- [Installation](troubleshooting/install.md)
- [Daemon lifecycle](troubleshooting/daemon.md)
- [Providers](troubleshooting/providers.md)
- [Repository indexing](troubleshooting/indexing.md)

## Design and security

- [Security documentation](security/README.md)
- [Architecture and ADRs](architecture/README.md)
- [Local control plane](control-plane.md)
- [Control protocol](protocol-v1.md)

AgentBus does not provide a kernel sandbox, guarantee generated code is safe,
or automatically roll back filesystem edits after a failed run. Review the
reported diff and changed files before committing or publishing anything.
