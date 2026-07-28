# Run Provenance

Every sealed AgentBus v0.4 trace has a versioned provenance manifest. The
manifest ties execution structure, captured objects, policy and tool versions,
approval and audit evidence, artifacts, and the final repository state to one
tamper-evident root.

This is an integrity mechanism. It is not a digital signature, trusted
timestamp, identity certificate, proof of authorship, or proof that an
external provider behaved correctly.

## Manifest contents

A `ProvenanceManifest` records:

- provenance and trace schema versions;
- run and trace IDs;
- AgentBus, operating system, and Python versions;
- Node and VS Code versions when known;
- a sanitized configuration fingerprint;
- provider, model, and deployment identifiers;
- managed tool descriptor versions and hashes;
- policy version and policy document hash;
- generated protocol hashes;
- sorted input, output, and generated-artifact object hashes;
- task-graph hash;
- event stream sequence range and count;
- final repository tree hash when available;
- replayability classification and reasons;
- ordered integrity entries and the integrity root.

Provider route identifiers are retained for reproducibility. Provider
credentials, bearer tokens, raw environment variables, prompts, and provider
SDK objects are not provenance fields.

## Integrity root

The current algorithm is `sha256-chain-v1`.

AgentBus creates canonical integrity entries for:

- ordered trace spans and events;
- referenced content-addressed blobs;
- approval records;
- immutable tool audit records;
- generated artifacts and repository evidence.

Each entry has a kind, stable identifier, and SHA-256 digest. Entries are
validated for deterministic ordering and uniqueness. The root starts from a
domain-separated seed and advances by hashing the previous chain value with
the hash of each canonical entry.

Changing a covered span, object, approval, audit record, artifact record, or
entry order changes the root or fails object verification. Replacing a blob
under an existing digest also fails because reads recompute both content
length and SHA-256.

## Sealing

Provenance is sealed only for terminal trace state. Sealing:

1. validates trace structure and terminal status;
2. classifies replayability against available objects;
3. fingerprints bounded configuration, policy, and protocol documents;
4. collects referenced content hashes;
5. includes durable approval, audit, and artifact evidence;
6. computes the ordered integrity root;
7. validates the completed manifest before persistence.

The durable run and task records remain execution truth. Sealing does not
rewrite successful task history or hide a later final-review rejection.

An optional trace-recording failure can be reported without corrupting run
state. A trace with missing or inconsistent critical evidence is not accepted
as verified provenance.

## Verification

Verify a run or trace locally:

```powershell
agentbus trace verify <run-or-trace-id>
agentbus trace verify <run-or-trace-id> --json
```

Verification checks:

- trace and provenance schema compatibility;
- manifest identity;
- canonical integrity entries and root;
- every referenced object hash and byte count;
- current generated protocol hashes.

Protocol changes are reported as drift. Missing, modified, or substituted
objects fail verification. Verification performs no provider or network call.

The control plane exposes:

```text
GET /api/v1/runs/{run_id}/provenance
GET /api/v1/runs/{run_id}/replayability
```

Responses are authenticated, loopback-only, bounded summaries. They include
hashes and safe runtime labels, not unrestricted provenance objects or private
blob access.

VS Code's **AgentBus: Open Provenance Manifest** command combines provenance,
trace, replayability, and bounded run outcomes in a read-only Markdown
document.

## Fork and replay provenance

A normal providerless replay creates a durable replay session linked to the
source trace. A fork creates:

- a new derived run ID;
- a new trace ID;
- `forked_from` links to the source trace;
- a sanitized changed-input object;
- a new provenance manifest and root;
- an automatic source-versus-fork comparison.

The source manifest is immutable. Fork reports expose changed input names and
content hashes rather than changed values. The comparison includes both
provenance roots and marks the root change explicitly.

The existence of a different root does not by itself mean regression. A fork
is expected to have different identity and changed-input evidence; structured
comparison categories explain whether observed changes are expected, policy
drift, output drift, or another class.

## Environment and nondeterminism

The manifest fingerprints relevant environment facts instead of storing raw
environment values. Components include operating system, Python, locale,
timezone, line endings, selected Git configuration, and a sanitized
environment hash.

Nondeterminism findings distinguish:

- controlled;
- captured;
- substituted;
- observed only;
- unresolved.

A valid provenance root proves consistency with the recorded evidence. It
does not make observed-only scheduling, latency, or an uncaptured external
effect deterministic.

## Archive provenance

A `.agentbus-trace` archive contains its provenance manifest, trace, protocol
documents, selected content-addressed objects, assertions, and an archive
manifest. Import validates:

- each entry hash and byte count;
- archive manifest root;
- trace and provenance identities;
- provenance root;
- protocol document hashes;
- object inventory.

The archive root protects portable archive composition. The provenance root
protects run evidence. They serve different layers and neither is a signature.

Source-like objects are omitted by default. Including or importing them
requires explicit consent and does not cause automatic execution.

## Threat model and limitations

Provenance detects accidental or malicious modification of covered local
records after capture. It does not protect against:

- a compromised AgentBus process generating false evidence at capture time;
- a compromised operating system;
- collision or preimage breaks in SHA-256;
- deletion of all trace and provenance records;
- false statements made by an external provider or tool;
- an attacker who can replace both the software and all evidence before a
  trusted verifier receives it;
- identity or timestamp disputes.

Future signing could add external identity proof, but v0.4 deliberately makes
no signing or authorship claim.

## Retention

Referenced, fixture, failure, pinned, and active replay objects are protected
according to retention policy. Garbage collection plans list protected and
candidate hashes before deletion, journal executed deletions, and can resume
after interruption.

Deleting an unreferenced retained trace object does not modify a source
repository. AgentBus never resets or cleans repository state as part of
provenance maintenance.

## Related documents

- [Execution Tracing](execution-tracing.md)
- [Deterministic Replay](deterministic-replay.md)
- [Trace Archives](trace-archives.md)
- [ADR 0010](adr/0010-deterministic-replay-and-provenance.md)
