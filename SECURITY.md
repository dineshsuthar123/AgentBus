# Security policy

## Supported versions

The latest `0.1` prerelease receives security fixes. Alpha releases are for
evaluation and are not covered by a production SLA.

## Reporting a vulnerability

Do not open a public issue containing credentials, exploit details, private
repository content, or provider responses. Use GitHub's private security
advisory flow for this repository. Include the affected version, a minimal
reproduction, impact, and suggested mitigation. Remove all real API keys and
personal data before submitting.

## Security boundaries

AgentBus restricts tools to a configured workspace, validates exact Git
repository boundaries, uses argument arrays with `shell=False`, redacts
secret-shaped data, and requires explicit approval for high-risk work and live
provider access. These controls are defense in depth, not a complete operating
system sandbox. Model-generated code and commands remain untrusted. Run
AgentBus with least-privilege credentials in disposable repositories and
review changes before commit, push, or PR creation.

Failed runs do not automatically reset, clean, delete, or roll back files.
Inspect reported artifacts and perform any cleanup manually.
