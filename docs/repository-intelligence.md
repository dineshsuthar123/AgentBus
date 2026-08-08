# Repository Intelligence

AgentBus repository intelligence is an optional, local, providerless index for
planning and reviewing work in larger repositories. It adds persisted projects,
files, modules, symbols, references, typed dependency edges, ownership rules,
architecture evidence, search, impact analysis, test selection, and bounded
context plans. It does not replace the existing repository scanner or context
pack builder. Runs continue through that compatibility path when no usable index
is available.

Repository intelligence is static analysis, not complete semantic
understanding. It does not compile projects, execute package managers, run setup
files, resolve every dynamic call, or prove runtime behavior.

## Quick start

Run commands from the intended repository root or pass an explicit workspace:

```powershell
agentbus index build --workspace C:\src\sample
agentbus index status --workspace C:\src\sample
agentbus search calculate_total --workspace C:\src\sample --evidence
agentbus dependencies SYMBOL_ID --workspace C:\src\sample --depth 3
agentbus impact services/api/calculator.py --workspace C:\src\sample
agentbus tests-for services/api/calculator.py --workspace C:\src\sample
agentbus context-plan "Change calculator rounding" --role planner --workspace C:\src\sample
```

Use `--json` for bounded machine-readable output. Human output shows at most a
bounded subset; `--evidence` includes safe explanations and hashes, never raw
secret-file content. The default index database is
`repository-index.sqlite3` beside the configured AgentBus state database.
`--index-db` selects another local database explicitly.

## Index lifecycle

| State | Meaning | Safe response |
| --- | --- | --- |
| `absent` | No snapshot exists. | Build the index or continue without it. |
| `building` | One lease-owning operation is active. | Observe progress or request cancellation. |
| `current` | Indexed inputs match the contained workspace. | Queries may use the snapshot. |
| `partially_current` | A bounded subset could not be parsed or discovered. | Use evidence with the reported uncertainty. |
| `stale` | Indexed files or ownership metadata changed. | Update before relying on impact conclusions. |
| `corrupted` | Integrity verification failed. | Repair or explicitly clear the local index. |
| `incompatible` | Schema, workspace, or parser versions differ. | Rebuild for the selected workspace. |
| `paused` | An operation stopped at a safe checkpoint. | Resume through update or repair. |

Build, update, verify, repair, cancellation, garbage collection, and clear are
explicit operations. AgentBus never resets, cleans, deletes, or rolls back
repository files as part of index recovery. See
[Incremental Indexing](incremental-indexing.md).

## Supported languages

| Language | Static evidence | Important limitations |
| --- | --- | --- |
| Python | Modules, classes, functions, methods, decorators, imports, calls, inheritance, tests, and common endpoint shapes. | Dynamic imports, monkeypatching, descriptors, and runtime dispatch may remain unresolved. |
| TypeScript and JavaScript | Modules, exports, classes, interfaces, functions, methods, imports, calls, tests, and common endpoint shapes. | No TypeScript compiler or package-manager resolution is run; aliases and dynamic JavaScript may be partial. |
| Java | Packages, classes, interfaces, records, methods, inheritance, implementations, calls, tests, and common framework annotations. | No build tool, annotation processor, bytecode, or full overload resolution is used. |
| Go | Packages, types, functions, methods, imports, calls, tests, and common HTTP handler shapes. | No `go list`, compilation, build-tag expansion, or interface-flow proof is performed. |

JSON, YAML, TOML, Markdown, and text metadata can contribute project,
configuration, and architecture evidence, but they are not treated as fully
typed programming languages.

## Projects, ownership, and architecture

Discovery recognizes bounded Python, Node, Java, and Go project metadata and
multi-project relationships. Metadata is read statically. `setup.py`, package
scripts, Maven/Gradle tasks, and Go commands are never executed by the indexer.

`CODEOWNERS` rules are persisted as explicit ownership evidence. Architecture
boundaries are inferred from project roots, imports, dependency crossings,
cycles, configuration, and ownership. Every inferred boundary includes a
confidence and explanation. Treat inferred boundaries as review evidence, not
policy or proof. Multiple roots remain separately identified; a snapshot cannot
be silently reused for another workspace identity.

## Runtime integration

For a current compatible snapshot, AgentBus builds deterministic planner,
coder, verifier, and reviewer summaries from paths, symbol identities, graph
evidence, hashes, confidence, and uncertainty. Runtime scope validation rejects
claims outside the current task and repository. Trace and provenance records
include the index snapshot and context-plan identities without persisting prompt
text or personal absolute paths.

If the index is absent, incompatible, corrupted, or unavailable, the existing
scanner and context-pack path remains available. A stale or partial index is
never represented as current; warnings and uncertainty travel with retrieval,
impact, context, and replay evidence.

## Privacy and security

- Source remains local. Indexing makes no provider or network calls.
- `.env`, credentials, private keys, generated output, dependencies, caches,
  and other protected paths are excluded before parsing.
- Symlinks, junctions, traversal, device paths, UNC paths, alternate data
  streams, and workspace escape are rejected by the contained-path resolver.
- Parsers inspect bounded text and never import or execute repository code.
- SQLite stores metadata, hashes, symbol facts, and graph facts, not source
  snapshots or embedding vectors.
- Control-plane and VS Code responses are bounded, authenticated, redacted,
  source-free summaries. Index mutation requires Workspace Trust in VS Code.
- Optional semantic retrieval accepts only an explicitly configured local
  provider that declares it does not send source off-device. No model is
  downloaded automatically.

See [Daemon Security](daemon-security.md) and [Sandbox Security](sandbox-security.md).

## Scaling and confidence

All discovery, parsing, search, graph traversal, API pagination, progress,
context, and diagnostic collections have hard limits. Large repositories may
produce truncated results or `partially_current` snapshots. Truncation is
reported and should increase review caution. Current deterministic fixture
benchmarks are useful regression checks, not statistically significant capacity
or accuracy claims for arbitrary production repositories.

Related details:

- [Hybrid Retrieval](hybrid-retrieval.md)
- [Change Impact Analysis](change-impact-analysis.md)
- [Context Planning](context-planning.md)
- [ADR 0011](adr/0011-repository-intelligence-engine.md)
