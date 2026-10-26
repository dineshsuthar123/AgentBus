# Studio demo quality contract

The local demo must be useful without a provider account and honest about what
has actually executed. Product quality includes failure states, keyboard use,
small screens, and bounded resource use, not just the successful screenshot.

## First impression

The execution ledger uses the bundled Bricolage display face, Plex body and
monospace faces, shared light/dark materials, and responsive spacing. The
first-run guide lets users explore scope, execution, and proof through a
keyboard-operable inspection object. It is explicitly an orientation object,
not an executing agent or a progress indicator.

The guided entry creates a new disposable calculator repository only after an
explicit user action. It does not replace the selected repository, silently
call a model, grant permissions, create a commit, or publish anything.

## Interaction details

- Recent signal counts are labeled as a recent snapshot, not lifetime totals.
- History is server-paged, searchable, and ordered by immutable creation time.
- Loading another page does not make older stale pages appear fresh.
- Inspecting a run and going back retains loaded pages, filters, and position.
- Run rows distinguish the run outcome from its checks and final review.
- Missing file/check/review observations are unreported, not fabricated zeros
  or success states.
- Search clearing and filter resetting are explicit, keyboard-operable actions.
- Loading and clipboard results have screen-reader announcements.
- A denied clipboard write leaves the identifier available for manual copying.
- Command navigation ignores composition Enter and held shortcut repetition.
- Keyboard-selected command results scroll into view.
- Reduced motion, renderer fallback, and forced-color support remain available.

## Backend and storage

[ADR 0012](../adr/0012-local-demo-data-and-request-boundaries.md) documents the
indexed SQLite history, batched read projections, session/request ownership,
cache bounds, migration compatibility, and retry semantics.

Workspace validation, exact approval revisions, final review, cancellation,
immutable successful attempts, and redaction remain authoritative runtime
boundaries. A failed run does not destructively roll back filesystem edits.
Generated runtime data must remain in managed storage, outside source diffs.

## Verification

From the repository root on Windows:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m compileall -q syndra agentbus
.venv\Scripts\python.exe -m syndra.eval run --suite core-offline --variant durable-parallel-fake
git diff --check
```

From `studio/`:

```powershell
npm.cmd test
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
```

After the build completes, stage generated assets from the repository root:

```powershell
.venv\Scripts\python.exe scripts/prepare_studio.py
```

Then run `node scripts/folio-smoke.mjs` from `studio/`. This starts and stops its
own authenticated loopback daemon and disposable Git fixtures. It checks real
repair execution, failure evidence, final review, offline replay, exact approval
and rejection across clients, history continuity, browser errors, no unexpected
external requests, bundled fonts, WebGL fallback, reduced motion, responsive
reflow, and browser accessibility. It writes local-only evidence under `.tmp/`.
Do not stage assets concurrently with the build or delete the build directory
to work around another process holding a generated file.

On machines with an inaccessible default pytest directory or an unusually long
repository path, select a fresh, short `--basetemp` path that does not already
exist. Do not erase or reuse someone else's temporary directory.

No verification step requires deployment, a paid model request, production
credentials, or exposing the local executor publicly. A passing local demo is
not a claim of production sandbox isolation or hosted-service readiness.
