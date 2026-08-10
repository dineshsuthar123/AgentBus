# Security policy

## Supported versions

The latest `0.6` public-beta prerelease receives security fixes. Older pre-1.0
milestones are unsupported unless maintainers state otherwise. AgentBus has no
production SLA.

## Report a vulnerability privately

Do not open a public issue with vulnerability details. Use this repository's
[private security advisory form](https://github.com/dineshsuthar123/AgentBus/security/advisories/new).

Include the affected AgentBus version, operating system, Python version,
minimal sanitized reproduction, impact, and a proposed mitigation if known.
Never include real API keys, bearer tokens, `.env` files, private source,
prompts, provider responses, databases, trace archives, or personal data.

## Security boundaries

AgentBus validates the canonical configured workspace and exact Git top-level,
derives capabilities independently of model claims, applies deterministic
policy, and binds risky approvals to an exact revision and scope. Managed
subprocesses use an absolute revalidated executable, separate arguments with
`shell=False`, a minimal environment, bounded resources and output, and process
tree cleanup. Filesystem adapters reject traversal, protected credentials,
unsafe links and junctions, Windows devices and alternate data streams, and
mutation outside the assigned repository or owned worktree.

Local MCP servers require explicit configuration, numeric loopback or managed
stdio transport, and an exact capability map. MCP peers, model output, indexed
evidence, imported archives, and generated repository code remain untrusted.

The local daemon uses loopback transport, ephemeral ports by default, process
identity checks, and bearer authentication. Registry files contain metadata,
not bearer tokens. The VS Code extension stores tokens in SecretStorage,
validates package/protocol/schema compatibility, and requires Workspace Trust
for execution and mutation.

Logs, errors, reports, support bundles, traces, package archives, and VSIX files
are bounded and scanned for sensitive content. Configuration mutation rejects
credential keys and unsafe links. Setup never writes provider credentials.

## Limits

These controls are defense in depth, not a VM, container, firewall, restricted
OS token, kernel sandbox, or proof that generated code is safe. Use
least-privilege OS and provider credentials, constrain network access, and
review changes before execution, commit, push, or pull-request creation.

Filesystem and external side effects are not transactionally rolled back.
Failed runs report created and modified files but do not automatically reset,
clean, delete, or restore them. Cleanup removes only validated AgentBus-owned
runtime artifacts and must preserve unknown or active user data.

See [security documentation](docs/security/README.md),
[Sandbox Security](docs/sandbox-security.md),
[Tool Capability Policy](docs/tool-policy.md), and
[MCP Integration](docs/mcp-integration.md).
