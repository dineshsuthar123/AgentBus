# Security documentation

Start with the repository [security policy](../../SECURITY.md) for private
vulnerability reporting.

- [Sandbox and process boundaries](../sandbox-security.md)
- [Tool capability policy](../tool-policy.md)
- [Managed tool runtime](../tool-runtime.md)
- [Daemon security](../daemon-security.md)
- [MCP trust boundaries](../mcp-integration.md)
- [Trace archive validation](../trace-archives.md)
- [Generated-artifact hygiene ADR](../adr/0003-generated-artifact-hygiene.md)

AgentBus uses canonical workspace and Git boundaries, structured tools,
independent capability derivation, deterministic policy, exact approvals,
bounded subprocesses with `shell=False`, redaction, and mandatory final review.
These controls do not replace a VM, container, restricted OS account, network
policy, or human review.
