# Real local repository validation

AgentBus can validate an arbitrary repository that already exists on the local
machine. This workflow is providerless: it inventories bounded source files,
builds a disposable repository index, and runs declared search, context,
impact, or task-context scenarios without executing repository setup scripts.

## Authorization boundary

Only validate a repository when its owner has authorized the inspection. The
operator supplies the path explicitly; AgentBus does not discover sibling
repositories, search user profile directories, or infer consent from filesystem
access. Validation does not grant permission to read material that policy marks
as protected.

```console
agentbus validate repo --path C:\work\authorized-repository --json
agentbus validate reliability --repository C:\work\authorized-repository --json
```

The path is resolved canonically. A Git workspace must resolve to its own Git
top-level directory rather than an unintended parent repository. Source files
are read and indexed, but repository commands, package managers, hooks, tests,
and model providers are not invoked by `validate repo`. Index state is created
under AgentBus-owned temporary storage, not in the supplied source tree.

Normal AgentBus task execution is a different workflow and may edit files after
approval and policy checks. A failed task does not automatically roll back
filesystem edits. Always inspect changed-file reporting before committing.

## Corpus modes

The bundled `agentbus-v07` corpus contains three enabled generated fixtures:

| Fixture | Scale | Evidence |
| --- | --- | --- |
| Generated Python library | tiny | Python source, tests, protected `.env` exclusion |
| Generated mixed monorepo | small | Python, TypeScript, Java, Go, generated/vendor exclusions |
| Generated deep tree | adversarial | Python source below 32 nested directory levels |

Run those fixtures entirely offline:

```console
agentbus validate corpus --offline --json
```

The corpus also describes ten disabled public repositories. They are not
cloned during normal tests, release evaluation, RC acceptance, or offline
corpus validation. Public download requires both `--include-optional` and
`--download-public`, plus an explicit cache directory. That mode performs
network access and is outside the local-only RC workflow.

The public validation corpus is descriptive and its current entries do not all
pin immutable revisions. Do not treat a downloaded corpus checkout as
reproducible release evidence unless the selected manifest entry pins and
verifies an immutable commit. The separate real-repository evaluation manifest
pins Pallets Click to an exact reviewed commit, but running that evaluation
also requires explicit live and download consent.

## Resource limits

Default validation limits are intentionally broad ceilings, not capacity
promises. The schema maxima prevent a manifest from requesting an unbounded
value:

| Resource | Default | Schema maximum |
| --- | ---: | ---: |
| Inventory files | 100,000 | 1,000,000 |
| Symbols | 1,000,000 | 2,000,000 |
| Projects | 10,000 | 100,000 |
| Repository bytes | 5 GiB | 100 GiB |
| Index duration | 300 seconds | 86,400 seconds |
| Query duration | 30 seconds | 3,600 seconds |
| Index database | 5 GiB | 100 GiB |
| Measured peak Python allocations | 2 GiB | 64 GiB |
| Scenario result count | 25 | 200 |
| Scenario context bytes | 100,000 | 10,000,000 |
| Scenario token budget | 16,000 | 1,000,000 |

Individual corpus entries can lower these ceilings. Exceeding a bound is a
classified validation failure, not permission to continue unbounded.

## Evidence and platforms

The v0.7 test matrix requires CPython 3.11 through 3.14 on Ubuntu and Windows,
plus dedicated Linux and Windows security-boundary jobs. The local milestone
verification described by the release report was run on Windows AMD64 with
CPython 3.14. Configured CI coverage is not proof that a platform passed until
the corresponding required check completes.

Synthetic repository benchmarks cover 100, 1,000, 10,000, and 50,000 generated
source-file profiles. The 1,000-file profile is the required CI scale smoke;
10,000 and 50,000 files remain explicit manual runs. Counts and timings from
generated fixtures do not predict behavior for every real repository.

## Known gaps

- Static parsing cannot reproduce every build-system, generated-code, macro,
  reflection, or runtime dependency relationship.
- Validation does not execute repository tests or prove that a proposed change
  is correct.
- Local filesystem authorization is an operator responsibility; AgentBus is
  not a data-classification system.
- The current validation process is not a kernel sandbox and does not replace a
  VM, container, restricted OS account, or network policy.
- Passing one checkout does not generalize to a newer commit, another platform,
  a larger history, or a different dependency environment.

Use [adversarial testing](adversarial-testing.md) for hostile fixture scope and
[reliability validation](reliability.md) for lifecycle and leak evidence.
