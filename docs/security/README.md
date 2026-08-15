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

## Defensive release scorecard

`python -m agentbus.release_security` combines the tracked-file and release
archive scan with controlled local validation of nine boundaries: filesystem
containment, approval and capability scope, Git safety, malformed tool
protocols, synthetic hostile MCP responses, trace archive integrity,
diagnostic privacy, Python package contents, and VSIX contents. The command
contacts no provider, network service, external repository, or third-party
security target.

The report lists every tested boundary and unresolved limitation. Missing real
wheel, source-distribution, or VSIX bytes are reported as limitations rather
than being hidden behind synthetic fixtures. A failed boundary blocks the
release audit; a platform limitation remains a visible warning. Use
`--static-only` only when a caller intentionally needs the original bounded
file/archive scan without runtime probes.

This scorecard is controlled local defensive validation, not formal
penetration-test certification. It does not replace independent security
review, isolation, operating-system policy, network controls, or threat-model
validation for a deployment.
