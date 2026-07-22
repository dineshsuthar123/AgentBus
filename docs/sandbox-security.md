# Sandbox Security

AgentBus applies capability policy, path containment, executable identity,
resource budgets, and process-tree supervision around local tools. These are
defense-in-depth controls. They are not a VM, container, seccomp profile,
restricted Windows token, or kernel security boundary, and they do not prove
that generated or repository code is safe.

Use AgentBus with a least-privilege OS account, no unnecessary credentials,
and a disposable repository or AgentBus-owned worktree. Review retained
changes before commit, push, deployment, or cleanup.

## Controlled process launch

`ControlledProcessSupervisor` accepts an executable identity and an argument
array. It never accepts a shell command from the model and always launches with
`shell=False`.

The executable catalog:

- resolves configured aliases to absolute files before execution;
- records path, size, modification time, device/inode identity, and SHA-256;
- revalidates that identity before each launch;
- rejects an unconfigured alias, ambiguous path, or later substitution;
- constructs `PATH` only from directories containing catalogued executables.

Arguments must be strings and NUL-free. The working directory is canonical,
must exist, and must remain inside the assigned worktree. The worktree identity
is checked again at launch. Standard aliases are intentionally narrow and
policy can still require approval or deny the request.

Windows cannot execute `.cmd` or `.bat` files directly. For an explicitly
catalogued batch file only, AgentBus resolves the trusted system `cmd.exe`,
rejects command-language metacharacters, quotes every bounded token, disables
AutoRun and delayed expansion, pins the `CreateProcess` application name, and
still uses `shell=False`. Arbitrary shell interpreters and command strings
remain denied.

## Environment isolation

Managed processes receive a minimal environment rather than `os.environ`.
AgentBus keeps only bounded platform and locale fields, rejects sensitive
override names, constructs a trusted `PATH`, and places `HOME`, `USERPROFILE`,
`TEMP`, `TMP`, and `TMPDIR` under an invocation-specific temporary directory.
Fixed values disable Python bytecode and interactive prompts where applicable.

Provider keys, bearer tokens, package registry credentials, cloud credentials,
Git credentials, proxy credentials, and unrestricted inherited variables are
not forwarded by default. Safe diagnostics report variable names and whether a
sensitive name was present, never environment values.

This reduces accidental leakage. A child can still read resources available to
the operating-system account unless the host applies stronger isolation.

## Output, timeout, and cancellation

Stdout and stderr are drained concurrently as bytes. Retained output is
redacted and bounded independently and in combination; excess bytes are
discarded while pipes continue to drain. Callbacks receive bounded ordered
chunks. Timeouts and persisted cancellation both terminate the managed process
tree, wait for a bounded grace period, and escalate where supported.

Results record PID, executable identity, working directory, exit code,
duration, truncation, termination reason, process-tree backend, cancellation
facts, and per-limit support. They do not persist subprocess handles or a raw
environment.

## Windows process handling

On Windows, the supervisor starts a new process group without a console and
attempts to assign the process to a Job Object. A successful Job Object:

- terminates descendants as one unit;
- limits active processes to the requested child count plus the root process;
- enforces aggregate memory only when `memory_bytes` is configured;
- enforces job user time only when `cpu_seconds` is configured.

If Job Object creation or assignment fails, AgentBus reports the limitation and
uses the absolute system `taskkill.exe /T /F` as a bounded process-tree
fallback. If both mechanisms are unavailable, only direct-process termination
can be attempted and the result reports tree termination as unsupported.

Job assignment can fail when a parent host imposes incompatible job rules.
Observed peak memory and exact descendant counts are not currently collected.

## POSIX process handling

On Linux and other POSIX hosts, AgentBus starts a new session and process group.
Cancellation or timeout sends `SIGTERM` to the group, waits for a bounded grace
period, then sends `SIGKILL`. The direct child is waited to avoid a zombie.

The current backend enforces wall clock and retained output, but does not apply
`setrlimit`, cgroups, namespaces, memory limits, CPU limits, or a child-count
limit. Those fields are reported as unsupported and unenforced. Process groups
reduce orphan risk but cannot contain a child that deliberately escapes into a
new session or is moved by another privileged process.

## Filesystem boundary

Filesystem tools accept canonical repository-relative paths only. The resolver
rejects:

- absolute, UNC, and Windows device paths;
- empty components, `.` or `..` traversal, NUL, and control ambiguity;
- Windows alternate data streams and reserved device names;
- components ending in a space or dot;
- symlink or junction escapes;
- mutation through any symlink or junction;
- changes to the canonical root device/inode identity.

Protected segments include `.agentbus`, `.aws`, `.azure`, `.codex`, `.docker`,
`.git`, `.kube`, and `.ssh`. Protected names and suffixes include `.env`, cloud
and Git credentials, private keys, daemon registries, SQLite control state,
`.npmrc`, and common secret files. Placeholder files such as `.env.example`
remain classifiable as ordinary project files, but real environment files are
denied.

Reads are size-bounded, classify text versus binary, and redact secret-shaped
text. Creates refuse existing paths. Writes use same-directory temporary files
and atomic replacement. Patches require expected content and optional hashes;
deletes require an expected hash; renames refuse overwrite. Mutations validate
containment before and after parent creation where applicable and record before
and after hashes, byte counts, task, invocation, generated status, and atomic
status.

These checks narrow time-of-check/time-of-use races but cannot eliminate every
race against another process with access to the same filesystem. Do not run
hostile concurrent writers against the worktree.

## Git boundary

Managed Git tools validate that the configured workspace is exactly the Git
top-level. A nested directory that lets Git walk into a parent repository fails
with `WorkspaceRepositoryMismatch`. Paths remain target-repository-relative,
and changed-file or diff collection never falls back to an unintended parent.

Git executes an absolute catalogued binary with argument arrays,
`--literal-pathspecs`, explicit `--` path separators, a sanitized environment,
and bounded redacted output. Each command disables hooks, fsmonitor, commit
signing, system Git configuration, terminal prompts, and environment-supplied
Git overrides other than explicit commit identity. User or repository identity
can still supply normal commit attribution. Revisions, branches, commit
messages, and pathspecs are validated against option and control-character
injection, and managed tools never mutate global configuration.

The managed surface exposes read operations plus path-scoped stage and commit.
Mutations require an explicitly AgentBus-owned worktree and task/invocation
attribution. A commit may include only explicit changed, policy-eligible paths.
Managed tools do not expose push, remote mutation, force operations,
`reset --hard`, `clean`, arbitrary branch deletion, credential-helper changes,
or global Git configuration.

The broader opt-in PR workflow remains separate from model tool dispatch. Its
commit, push, and PR gates still require verifier success and final reviewer
approval.

## Artifact and side-effect limits

Structured results and artifact metadata are schema-bounded. File bytes,
mutation counts, cumulative writes, invocation counts, concurrent processes,
and retained output consume the persisted run budget. Generated files are
classified for review and commit eligibility but are not deleted automatically.

Failure, denial, cancellation, timeout, or reviewer rejection does not roll
back completed filesystem, Git, process, MCP, or network side effects. Reports
list retained files and safe cleanup guidance. AgentBus never runs automatic
`git reset`, `git clean`, recursive deletion, or destructive rollback.

## Known limitations

- There is no kernel-grade isolation or malicious-code containment claim.
- Network denial is a policy and environment control, not an OS firewall.
- POSIX memory, CPU, and child-count limits are currently unsupported.
- Windows fallback quality depends on Job Object assignment or `taskkill`.
- An allowlisted interpreter can execute repository code with the current OS
  account's permissions.
- Filesystem containment cannot remove every cross-process TOCTOU race.
- External side effects are not transactionally reversible.
- MCP peers and generated code remain untrusted even after schema validation.
