# Trace Archives

AgentBus v0.4 uses `.agentbus-trace` as a portable, deterministic archive for
terminal execution traces and regression fixtures. The format is a constrained
ZIP document, not a general extraction format and not an executable package.

Import validates and stores evidence. It never starts replay, runs code,
extracts files into a repository, or calls a provider.

## Archive layout

Schema version 1 uses the format name `agentbus.trace-archive`. A normal
archive contains:

```text
manifest.json
assertions.json
provenance.json
trace.json
protocols/<safe-name>.json
objects/<sha256>.blob
objects/<sha256>.metadata.json
```

Source-like object entries can be omitted. Every entry other than
`manifest.json` is declared in the manifest with:

- portable relative path;
- SHA-256;
- byte count;
- media type;
- source-content flag.

The manifest also identifies the run and trace, provenance root, included and
omitted object hashes, source-content state, creation time, and archive root.

## Deterministic export

Export:

- validates trace and provenance first;
- accepts terminal traces only;
- sorts protocol documents, objects, and archive entries;
- uses canonical JSON;
- uses fixed ZIP timestamps and attributes;
- rejects replacing an existing destination;
- computes an archive root over canonical manifest values.

Equivalent trace evidence and export options produce byte-identical archives.
The archive root protects archive composition. The provenance root protects
covered run evidence. Neither root is a signature.

Export without source-like objects:

```powershell
agentbus trace export <run-or-trace-id> `
  --output run.agentbus-trace `
  --json
```

Explicitly include bounded sanitized source-like objects:

```powershell
agentbus trace export <run-or-trace-id> `
  --output run-with-source.agentbus-trace `
  --include-source-content `
  --json
```

Potential source content is derived from media types and trace reference
names, not trusted from a caller-supplied manifest flag. It includes
source-like patches, diffs, model or tool envelopes, and generated source
objects needed by replay. Entire repositories are not exported by default.

An archive containing source-like material carries a warning. Review its
origin and license before sharing it.

## Import trust boundary

Treat every archive as untrusted input, including an archive created by an
older AgentBus version.

Import without source consent:

```powershell
agentbus trace import run.agentbus-trace --json
```

If validation derives that source-like objects are present, import stops
before writing any object. Consent must be explicit:

```powershell
agentbus trace import run-with-source.agentbus-trace `
  --allow-source-content `
  --json
```

Import returns trace ID, run ID, provenance root, archive SHA-256, and whether
objects were newly imported. `replay_started` is always false.

Run replay separately:

```powershell
agentbus replay run-with-source.agentbus-trace `
  --mode offline `
  --allow-source-content `
  --json
```

## Validation order

The importer validates before persistence:

1. source path is a regular non-symlink file;
2. ZIP metadata, entry count, sizes, and compression ratios are bounded;
3. entry names are unique and portable;
4. no entry is executable, encrypted, a link, directory, device, or special
   file;
5. the entry set exactly matches the manifest;
6. every entry byte count and SHA-256 matches;
7. core JSON documents validate against supported schemas;
8. run, trace, provenance, and manifest identities match;
9. provenance integrity verifies;
10. protocol documents match provenance hashes;
11. object metadata, media type, content hash, and inventory match;
12. secret-classified content is rejected;
13. source-content consent is derived and enforced;
14. validated objects are written to content-addressed storage.

An object is persisted with the `fixture` retention class. A collision with
different local trace or provenance data fails instead of overwriting it.

## Bounds

The core archive implementation enforces:

- at most 10,000 entries;
- at most 128 MiB total uncompressed content;
- at most a 100:1 compression ratio per entry;
- configured content-addressed object size limits;
- bounded JSON structure and text.

The authenticated local control plane and VS Code transport use a stricter
650,000-byte archive limit and a 900,000-character canonical base64 limit.
Those limits keep one local HTTP request bounded and are not a promise that
every CLI-valid archive fits through the control API.

## Path and extraction safety

Entry paths:

- use `/` separators;
- are relative;
- contain no empty, `.` or `..` segments;
- contain no drive, colon, backslash, or absolute prefix;
- must exactly match declared paths.

The importer reads validated entries into bounded memory and writes only
content-addressed objects. It does not call `extract`, restore arbitrary
filenames, honor archive permissions, or execute imported content.

Duplicate names are rejected before conversion to a mapping, preventing
duplicate-entry ambiguity. Symlink, junction, device, encrypted, executable,
and special-file metadata is rejected.

## Corruption and tampering

Any of the following fails safely:

- a modified blob or metadata document;
- a wrong entry hash or byte count;
- a changed provenance root;
- a changed protocol document;
- undeclared or missing objects;
- a wrong source-content marker;
- duplicate or traversal entries;
- an unsupported schema version;
- invalid ZIP structure;
- a compression bomb;
- secret-classified content.

Use:

```powershell
agentbus trace verify <run-or-trace-id> --json
```

Verification of an imported trace rechecks provenance and stored objects.

## Control plane and VS Code

The loopback control plane provides:

```text
GET  /api/v1/traces/{trace_id}/export
POST /api/v1/traces/import
```

Archive bytes are canonical base64 in authenticated JSON. Tokens never appear
in URLs. The server and VS Code independently validate decoded size, canonical
base64, identity, and SHA-256.

VS Code commands:

- **AgentBus: Export Trace**
- **AgentBus: Import Trace**
- **AgentBus: Capture Regression Fixture**

Source inclusion and import require explicit user choices. Import confirmation
states that replay was not started.

## Operational guidance

- Keep source-bearing archives outside the repository unless intentionally
  creating a reviewed fixture.
- Never commit an archive before inspecting its source and license warning.
- Do not accept a valid hash as proof of a trusted author.
- Import into isolated state before replaying third-party evidence.
- Use offline mode first.
- Delete temporary copies through an explicit reviewed cleanup process.
- Do not rename an arbitrary ZIP to `.agentbus-trace` and assume it is safe;
  validation, not the extension, establishes format validity.

## Related documents

- [Execution Tracing](execution-tracing.md)
- [Run Provenance](run-provenance.md)
- [Regression Fixtures](regression-fixtures.md)
- [Deterministic Replay](deterministic-replay.md)
