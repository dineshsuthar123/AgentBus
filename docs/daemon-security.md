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
- Daemon shutdown validates PID, process-start identity, and executable identity
  before signalling. A reused PID is never terminated.
- The API exposes no arbitrary shell command endpoint and all subprocesses use
  `shell=False`.

This is a local safety boundary, not perfect sandbox isolation.
