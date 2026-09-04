# Architecture

```text
Syndra Studio (React + TypeScript + Vite)
        |  same-origin /api proxy + bearer token in memory
        v
Syndra control plane (numeric loopback HTTP + SSE)
        |
        +-- workspace validation and repository intelligence
        +-- background durable run supervisor
        +-- exact approval and cancellation APIs
        +-- bounded diff, reports, tools, attempts, and audit projections
        +-- trace, provenance, verification, and replay services
        |
        v
Syndra runtime
  planner -> task graph -> coder -> managed tools
                           |             |
                           v             v
                     verifier       policy / approval
                           |
                           v
                   mandatory final reviewer
        |
        +-- SQLite durable state
        +-- .syndra/runs redacted JSONL
        +-- content-addressed trace objects and provenance
```

## Browser Boundary

The daemon deliberately has no general browser CORS surface. Development and
preview use Vite as a same-origin proxy to a fixed numeric-loopback target,
`http://127.0.0.1:8765` by default. `SYNDRA_STUDIO_TARGET` can select another
numeric-loopback daemon before Vite starts.

The one-time token printed by `syndra serve --json-ready` is held only in
React memory. It is not placed in a URL, local storage, or repository file.

## New Protocol Surface

- `GET /api/v1/runs/{run_id}/attempts`: bounded, redacted immutable attempt and
  RetryEvidence summaries; raw metadata, commands, stdout, stderr, and prompts
  are excluded.
- `POST /api/v1/runs/{run_id}/trace/verify`: invokes the existing providerless
  provenance/object verifier and reports explicit provider/network counters.

All other Studio functions reuse the existing control protocol.
