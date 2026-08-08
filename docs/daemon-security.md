# Daemon Security

- Bind addresses must be numeric loopback addresses (`127.0.0.1` or `::1`).
- Every `/api/v1` request requires an opaque bearer token.
- The token appears only in the parent-process JSON startup handshake and VS
  Code SecretStorage. It is excluded from registry files, URLs, logs, SQLite,
  settings, and generated protocol examples.
- Browser Origins are rejected unless they resolve to a numeric loopback
  address. No permissive CORS policy is installed.
- Requests, events, files, and diffs are bounded and redacted.
- Repository reads require exact Git top-level equality. Traversal, secret
  names, binary data, unsupported text, and oversized files are rejected.
- Tool inspection exposes bounded descriptors, capabilities, policy outcomes,
  budgets, usage, safe errors, and audit state. It never returns raw process
  handles, unbounded output, approval secrets, or environment values.
- Tool approval decisions are revision checked. Resume revalidates the exact
  invocation, arguments, capability scope, workspace, budget, cancellation
  revision, and approval identity before dispatch.
- Tool cancellation targets the invocation's owning run. The API does not
  accept a client-supplied PID or signal an unrelated process.
- Configured MCP diagnostics permit only managed stdio or numeric-loopback HTTP
  servers. They expose safe aliases and cleanup state, never commands,
  environment values, or bearer tokens.
- Authenticated `POST /mcp` exposes a fixed AgentBus tool set. It has no
  arbitrary file, process, database, approval-decision, commit, push, PR, live
  provider, or server-configuration operation.
- Daemon shutdown validates PID, process-start identity, and executable identity
  before signalling. A reused PID is never terminated.
- The API exposes no arbitrary shell command endpoint. Managed subprocesses use
  an absolute executable identity, separate arguments, a sanitized environment,
  `shell=False`, bounded resources, and process-tree cleanup.

## Repository-index security

Repository indexing remains inside the daemon's loopback, bearer-authenticated,
workspace-scoped boundary. Mutating index operations require Workspace Trust.
The daemon uses the same contained-path resolver as managed filesystem tools and
rejects traversal, symlinks/junctions, device and UNC paths, alternate data
streams, protected files, and cross-workspace identity reuse.

Parsers read bounded text statically. They never import Python modules, execute
`setup.py`, invoke package managers or build tools, or evaluate JavaScript, Java,
or Go code. SQLite writes are parameterized and atomically publish immutable
snapshots. Search does not interpolate user input into SQL or FTS syntax; graph
depth, pages, context, progress, and diagnostics are bounded.

Index records and API evidence contain metadata and hashes, not source
snapshots, prompts, secrets, personal absolute paths, or embedding vectors.
Optional semantic retrieval is disabled by default and rejects providers that
declare off-device source transfer. `repository-index.sqlite3`, WAL/SHM files,
and local semantic caches are runtime artifacts and must not be packaged or
committed.

See [Repository Intelligence](repository-intelligence.md).

This is a local authenticated policy boundary, not a VM, container, firewall,
restricted OS token, or perfect sandbox isolation. See
[Sandbox Security](sandbox-security.md) and [MCP Integration](mcp-integration.md).
