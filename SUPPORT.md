# AgentBus support

AgentBus `0.6` is a public beta without a production SLA. Search existing issues
before opening a reproducible bug, installation, provider, or
repository-intelligence report. Use the private process in
[SECURITY.md](SECURITY.md) for vulnerabilities.

Include only:

- `agentbus version --json` output;
- operating system and Python version;
- the failing command and stable error code;
- sanitized `agentbus doctor --json` output;
- a minimal disposable-repository reproduction;
- a reviewed support bundle when it is necessary.

Create a local sanitized bundle with:

```console
agentbus support-bundle --output agentbus-support.zip --json
```

Review it before attaching. Source-derived run metadata requires explicit
consent. Never share API keys, bearer tokens, `.env`, full private repositories,
raw prompts, provider responses, SQLite databases, trace stores, or unredacted
logs.

Start with [troubleshooting](docs/troubleshooting/install.md) and the
[documentation home](docs/README.md).
