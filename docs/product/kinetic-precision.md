# Kinetic Precision In Studio

This is the production React implementation of the approved design in
`design/kinetic-precision/PROPOSAL.md`, not the static demo or its sample store.
The existing typed API client, event store, run bundle loader, hash routing,
approval revisions and bounded navigation memory remain authoritative.

## Composition

- A short working queue replaces the folder-tab shelf. It contains open work,
  not a claimed total of all durable runs. Complete history stays server-paged.
- The selected artifact occupies the main reading surface. Source is not
  forced into a tiny sidebar; permissions are a distinct high-contrast
  attachment, stacked without clipping on smaller screens.
- A contextual workspace facility retains repository indexing/search,
  provider diagnostics, policy/tool inspection and appearance controls.
- The connection surface uses expressive Bricolage typography and the original
  layered Syndra object. Returning authenticated sessions restore valid work
  directly, with no compulsory intro or fixture data.
- `studio/src/kinetic.css` is the only application stylesheet imported by
  `main.tsx`. The prior stylesheets are inactive, not another cascade layer.

## Status And Authority

The five inspection lenses are plan, changes, verification, final review and
evidence. They are not a fixed five-task graph. The task register is built from
persisted TaskSpecs and retains terminal success even when final review rejects
the run. Missing artifact data is unavailable, not zero or successful.

No component submits approval, resumes a run or calls a provider merely by
displaying the scene. Approval controls still bind to the exact request and
revision; 409 conflicts reconcile without resuming. Verifier success, reviewer
acceptance and trace integrity are never interchangeable. Status colors use
exact known outcomes, not substring matches that would color `invalid` green.

## Rendering And Assets

The original S outline is rounded, triangulated without filling its concavities,
and extruded into bevelled slabs. The lazily loaded native WebGL renderer uses
one bounded vertex buffer and five draw calls. It caps pixel density at 1.6 and
stops ambient frames for pause, reduced motion, offscreen and hidden documents.
Context failure releases resources and leaves a non-WebGL SVG composition.
All meaningful information and interaction remain in native HTML.

The connection object animates unless reduced motion is requested. The run
object starts paused and can be hidden completely. Leaving the overview
unmounts it. Source, checks and evidence do not retain a decorative renderer.

Bricolage Grotesque is bundled locally with its SIL Open Font License in
`studio/public/LICENSE-bricolage.txt`; Plex remains bundled through the existing
Fontsource packages. No CDN, third-party model or external texture is needed.
`scripts/prepare_studio.py` includes the new font and license in the exact
hashed distribution manifest.

## Verification

The real-runtime browser acceptance script is `studio/scripts/folio-smoke.mjs`.
Set `SYNDRA_ACCEPTANCE_EVIDENCE` to a unique directory name to retain a new
evidence set under `.tmp` without overwriting earlier results. It uses disposable
repositories and the offline deterministic provider, not paid Azure calls.
Its intentionally failing planner fixture exhausts all configured provider
retries; a single transient failure is legitimately recoverable.

Run Studio tests, typecheck, lint and build before staging generated assets.
Then exercise the real daemon acceptance, including success/failure, exact and
stale decisions, reading position, draft preservation, deep links, cursor
history, archive round-trip, repository tools, dark/light and narrow reflow.
Renderer lifecycle and geometry tests cover cleanup and fallback separately
from the real browser's WebGL checks. Automated axe checks do not substitute
for manual screen-reader and cross-device assessment.

### Verified Checkpoint: 2026-10-08

- Full Python suite: 1,992 passed, 11 skipped. The one warning is the existing
  Starlette test-client deprecation.
- Full Studio suite: 139 tests passed across 35 files. Existing redaction,
  credential-handling and approval tests remain included.
- Studio lint, TypeScript checking, production build, Python compileall and
  Git whitespace checking passed.
- The wheel and sdist audit passed, verifying all 51 hashed Studio assets,
  including the local display font and its license.
- Real Chrome acceptance against the installed wheel passed with 24 automated
  accessibility states, no axe violations, no browser errors and no page-wide
  horizontal overflow. It exercised light/dark appearance, 320px mobile reflow,
  source inspection, 123 persisted history rows, draft/reading-position
  restoration, exact/stale/cross-client approvals and trace archive round-trip.
- Native WebGL rendered without GL errors; density bounds, animation/pause,
  reduced motion, hide/unmount and forced context-loss fallback passed.
- Success and deliberate failure ran through the real local daemon with the
  deterministic provider: zero live provider calls and zero external requests.

Local acceptance evidence is retained under
`.tmp/kinetic-installed-evidence-verified/`; the distribution audit is
`.tmp/kinetic-delivery-distribution-report.json`. These are verification outputs,
not files for commit. Narrow high-density reflow is tested as a zoom equivalent;
native browser zoom, manual assistive-technology assessment and cross-device
GPU testing remain separate release checks.
