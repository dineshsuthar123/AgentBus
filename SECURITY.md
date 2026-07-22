# Security policy

## Supported versions

The latest `0.3` prerelease receives security fixes. Alpha releases are for
evaluation and are not covered by a production SLA.

## Reporting a vulnerability

Do not open a public issue containing credentials, exploit details, private
repository content, or provider responses. Use GitHub's private security
advisory flow for this repository. Include the affected version, a minimal
reproduction, impact, and suggested mitigation. Remove all real API keys and
personal data before submitting.

## Security boundaries

AgentBus restricts managed tools to a canonical configured workspace, validates
exact Git repository boundaries, derives capabilities independently of model
claims, and applies deterministic policy before dispatch. Process adapters use
an absolute executable identity, separate argument arrays with `shell=False`, a
minimal environment, bounded output and resources, and process-tree cleanup.
Filesystem adapters reject traversal, protected credential paths, unsafe
links, devices, alternate data streams, and writes outside the assigned
repository or worktree. Audit records, reports, diffs, and errors are bounded
and redact secret-shaped values.

High-risk tools and live providers require a revision-bound approval. Approval
authorizes only the recorded run, task, tool, arguments, capability scope,
workspace, budget, and cancellation revision. Resume revalidates that binding.
The VS Code extension also requires Workspace Trust for start, resume,
approval, and Git mutations; rejection, cancellation, and read-only
diagnostics remain available without trust.

Local MCP servers require explicit configuration, numeric loopback or managed
stdio transport, and an exact capability map for every imported tool. MCP peers
and generated repository code remain untrusted even after protocol and schema
validation. Run them with least-privilege OS credentials.

These controls are defense in depth, not a VM, container, firewall, restricted
OS token, seccomp profile, or complete kernel security boundary. Review changes
before commit, push, or PR creation. See
[Sandbox Security](docs/sandbox-security.md),
[Tool Capability Policy](docs/tool-policy.md), and
[MCP Integration](docs/mcp-integration.md) for guarantees and limitations.

Failed runs do not automatically reset, clean, delete, or roll back files.
Inspect reported artifacts and perform any cleanup manually.
