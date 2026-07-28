# Regression Fixtures

An AgentBus regression fixture is a successful, sanitized execution trace plus
deterministic assertions. It uses the `.agentbus-trace` container and can be
replayed without provider credentials or network access.

Fixtures are intended for repository tests, replay regressions, policy
regressions, and protocol compatibility. They are not recordings of hidden
reasoning and are not substitutes for reviewing included source or licenses.

## Capture requirements

Only a successful terminal run with sealed provenance can become a fixture.
Capture verifies that derived assertions agree with recorded evidence before
writing the archive.

Capture without source-like objects:

```powershell
agentbus trace capture <run-or-trace-id> `
  --output tests/fixtures/example.agentbus-trace `
  --json
```

Some runs require generated source, a patch, or a tool envelope to validate
expected outcomes. Capture then refuses to create a non-executable partial
fixture and requires explicit source consent:

```powershell
agentbus trace capture <run-or-trace-id> `
  --output tests/fixtures/example.agentbus-trace `
  --include-source-content `
  --json
```

Source inclusion is never inferred from a filename or silently enabled.

## Fixture contents

The fixture assertion document records applicable values for:

- final trace status;
- replay session status;
- evaluation score;
- verifier pass result;
- reviewer approval;
- file-scope violations;
- policy decisions;
- managed tool outcomes;
- expected repository patch hashes;
- safety failures.

Assertions use stable statuses, identifiers, and hashes. Arbitrary captured
provider values, raw prompts, credentials, private absolute paths, and
unbounded output are not fixture assertions.

The fixture specification also contains:

- fixture schema and format versions;
- run, trace, and provenance identity;
- deterministic creation time;
- whether source content was requested;
- source and license warnings when applicable;
- a replay command.

Expected patch hashes are sorted and unique. Contradictory assertions are
rejected at capture time.

## Execute a fixture

Replay the fixture offline:

```powershell
agentbus replay tests/fixtures/example.agentbus-trace `
  --mode offline `
  --json
```

For a source-bearing fixture:

```powershell
agentbus replay tests/fixtures/example.agentbus-trace `
  --mode offline `
  --allow-source-content `
  --json
```

Execution:

1. validates the bounded archive;
2. validates provenance and content hashes;
3. imports objects into the configured local trace storage;
4. replays captured provider and tool evidence without providers;
5. evaluates fixture assertions against the replay result;
6. reports assertion failures.

Provider and network call counters must remain zero. Archive import alone does
not execute the fixture.

## Assertions and interpretation

A passing fixture means the current AgentBus version:

- accepted the archive and provenance;
- reproduced the applicable structured replay outcomes;
- matched the recorded assertions;
- made no provider or network call in offline mode.

It does not prove:

- identical host scheduling or duration;
- identical behavior from an uncontrolled external service;
- source authorship or license permission;
- absence of bugs outside the asserted dimensions;
- rollback of filesystem or external side effects from the original run.

Observed-only or unresolved nondeterminism remains visible in replay and
provenance reports.

## Source and license boundary

Source-like content includes more than files ending in `.py` or `.ts`.
AgentBus derives the boundary from trace media types and reference names and
can classify:

- patches and diffs;
- generated source objects;
- model envelopes containing source-like structured values;
- tool envelopes needed to validate mutations;
- other objects marked by source-related media types.

A source-bearing fixture includes both a source warning and a license warning.
Review:

- where the content came from;
- whether it can be redistributed;
- whether secrets or customer data could survive domain-specific formatting;
- whether the fixture belongs in the repository;
- whether its size is appropriate.

AgentBus redaction and secret detection are defense in depth, not a legal or
data-classification review.

## Repository hygiene

Before committing a fixture:

1. capture into a temporary location;
2. verify and replay it offline;
3. inspect archive metadata and warnings;
4. confirm no provider keys or private paths are present;
5. confirm the fixture is intentional and bounded;
6. add only the exact reviewed file.

Do not commit:

- state databases or WAL/SHM files;
- `trace-objects` stores;
- replay worktrees;
- exported debugging archives;
- Electron profiles or caches;
- logs;
- source-bearing fixtures that have not been reviewed.

The generated-artifact audit and VSIX audit reject runtime stores and archives
that do not belong in release artifacts.

## VS Code workflow

Use **AgentBus: Capture Regression Fixture** on a successful run.

The command:

- first attempts capture without source content;
- prompts only when the control plane returns a source-consent requirement;
- displays source and license warnings;
- validates run and trace identity;
- verifies canonical base64, size, and SHA-256;
- writes the selected `.agentbus-trace` file;
- confirms that replay was not started.

Programmatic extension tests can pass `includeSourceContent` and a destination
explicitly. The command never treats a prior general approval as source
consent.

## Updating fixtures

Do not overwrite a fixture merely because replay changed. Compare the old and
new runs first:

```powershell
agentbus compare <old-run-or-trace> <new-run-or-trace> --json
```

Classify whether differences are expected configuration changes, policy drift,
model/tool drift, environment drift, regressions, or improvements. Capture a
new fixture only after the changed assertions and source boundary are reviewed.

## Retention

Imported and captured fixture objects receive the `fixture` retention class.
Default garbage collection protects referenced objects. An exported archive
is still an independent file; deleting local trace storage does not delete the
archive, and deleting the archive does not clean source repository changes.

## Related documents

- [Trace Archives](trace-archives.md)
- [Deterministic Replay](deterministic-replay.md)
- [Execution Tracing](execution-tracing.md)
- [Run Provenance](run-provenance.md)
