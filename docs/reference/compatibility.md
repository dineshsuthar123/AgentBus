# Pre-v1 compatibility policy

AgentBus follows semantic versioning as a project and PEP 440 spelling for the
Python package. Before 1.0, the minor version is the compatibility line:
`0.6.x` components are intended to work together; a `0.7` release may contain
documented breaking changes.

## Public surfaces

- CLI command names and safe defaults are stable within a minor line. New
  options may be added; unsafe legacy behavior is not preserved.
- Config keys and precedence are stable within a minor line. Unknown and
  credential keys fail closed.
- Control, tool, and repository-intelligence protocols have explicit versions.
  Incompatible daemons or extensions are rejected before execution.
- Trace, state, and index schemas are versioned. Supported forward migrations
  are explicit, backed up, and verified; downgrade is not automatic.
- Trace exports are validated as untrusted archives. A future schema may
  require a newer AgentBus version.
- The VS Code extension and Python package must share the same minor line and
  compatible control protocol and state schema.

Run `agentbus version --json` and `agentbus upgrade-check --json` before an
upgrade. See [the changelog](../../CHANGELOG.md) for each milestone.
