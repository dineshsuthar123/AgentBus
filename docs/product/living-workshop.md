# The Living Workshop

Production Studio implementation, 11 October 2026.

## Opening The Experience

```powershell
.\.venv\Scripts\syndra.exe studio --workspace .
```

Choose **Explore Syndra** in the working queue, or **Explore the workshop**
in the genuinely empty history state. The authenticated route is
`/studio/#/workshop`. Successful launch authentication still enters work
directly. Returning sessions and durable deep links do not replay an intro.

The unauthenticated connection surface presents the same journey. **Open
Studio** jumps directly to the real session instructions; it does not change
the durable hash or manufacture authentication. The existing recovery form
remains closed by default, password-masked and memory-only.

## A Continuous Experience

1. **Arrival:** expressive local typography and an original, bevelled orange
   ribbon derived from Syndra's own shape. Dragging turns its bounded camera;
   a native slider continuously pulls the fold apart. Unfold, reset, pause
   and flat-rendering controls are available immediately.
2. **The system unfolds:** ordinary, reversible scrolling changes the same
   form into an inspection rail. Repair and refactor examples change intent,
   context, task, scope, protected boundary and artifact together. Structure,
   Boundary and Evidence are native controls. At the boundary the scene
   settles and explicit operation detail takes priority.
3. **Your workshop:** an artifact-like entry plane hands off to an empty task
   draft or persisted history. Example files and sample text are never copied
   into a task or repository.

The explanation is labelled **Illustrative example. Local only. No execution
or model calls.** Expected verification and final review are separate, not
fake successful outcomes. No example can approve an operation, call a model,
create a run or invoke a workspace tool.

Authenticated exploration stays inside the existing shell. The working set,
unsubmitted draft, history filters and artifact reading positions survive
visiting and leaving the journey. Opening a working surface removes the
journey renderer rather than leaving it behind a hidden overlay.

## Hands-On, Not Autoplay

- Turn the ribbon by dragging horizontally. Arrow keys turn it in fixed
  increments; Home and Reset restore the authored view without changing
  execution or resuming ambient animation. Pointer updates coalesce once
  per frame, and cancellation ends the gesture. Vertical touch scrolling
  remains native.
- Pull the fold from 0 to 100 using the native range. This is sculpture shape,
  explicitly not execution progress. Reset returns control to the chapter's
  scroll-linked pose; manually chosen shape stays put until then.
- Pick Context, Task or Artifact cards. Their local detail, selection rail
  and highlighted ribbon region change together. Native previous/next and
  range controls offer the same inspection without a pointer.
- The optional illustrative walkthrough advances through three pieces once,
  then stops. Manual selection, scrolling it offscreen, hiding the document,
  changing reduced-motion preference or leaving the page stops its timer.
  Reduced motion advances only one piece per explicit activation.
- Try the example scope boundary and return. The request illustration stops
  at the protected edge; neither position grants permission. Slide the source
  reveal to compare before and proposed lines, with both complete lines
  available in the readable diff beneath it.

These controls use only local presentation state. They never send a
control-plane mutation, write an example artifact or impersonate approvals,
verification or actual task outcomes. Flat rendering retains direct camera
and shape inspection without a GPU canvas.

## Execution Carries The Identity

The real execution overview uses the same continuous ribbon and an orange
selection rail. Its five regions are **inspection lenses**, not sequential
workflow tasks or synthetic progress. Native controls show actual plan,
observed files, verification, final review and captured evidence. Missing
data stays unavailable. The register uses real persisted task identities and
statuses, including completed tasks in rejected runs.

The run scene starts paused. Hide Object unmounts it. Source and check
inspection do not retain a decorative canvas. Exact-revision approval,
409 conflict handling, real replay, source selection and task navigation use
the existing typed API and remain independent of all scene interaction.

## Rendering And Accessibility

- Two original poses share a bounded mesh: 160 rings, eight bevel faces,
  approximately 0.4 MB of vertex data, one draw call per ribbon frame.
- Surface normals interpolate along the fold while retaining bevel edges.
  No third-party 3D library, model download, CDN or external font is required.
- Renderer code loads on mounting. Pixel density is capped at 1.6. Paused,
  reduced-motion, offscreen and hidden scenes do not run ambient frames.
  Offscreen settings are applied when the scene becomes visible again.
- Flat view releases the GPU renderer and preserves the native interaction.
  Shader/allocation failure and context loss also have an authored SVG
  fallback. Cleanup disconnects observers, listeners, buffers and programs.
- Chapter controls support keyboard and touch, restore focus to headings and
  respect reduced motion. There is no scroll hijacking or autoplay audio.
- At narrow widths the scene joins a vertical composition; the story and
  useful controls remain readable. Theme choice is preserved through the
  story rather than overridden for a cinematic effect.

## Verification

The extended `studio/scripts/folio-smoke.mjs` runs an owned local daemon with
deterministic providers and disposable repositories. It checks the journey,
direct drag and keyboard orbit, continuous fold control, linked example
selection, finite walkthrough, boundary and source reveal, local-only
interactions, deep-link preservation, flat-renderer release,
mobile overflow, annotation/control overlap and theme states alongside the
existing success, failure, exact/stale/cross-client approval, artifact,
history, draft and evidence acceptance cases. It does not make live model
calls or interact with user repositories.

```powershell
cd studio
npm.cmd run lint
npm.cmd test
npm.cmd run build
cd ..
.venv\Scripts\python.exe scripts/prepare_studio.py
$env:SYNDRA_BROWSER_CHANNEL = 'chrome'
$env:SYNDRA_ACCEPTANCE_EVIDENCE = 'living-workshop-evidence'
node studio/scripts/folio-smoke.mjs
```

For installed-product verification, build the paired sdist and wheel, audit
their manifests with `scripts/verify_distribution.py`, install the wheel into
an isolated test environment, and set `SYNDRA_ACCEPTANCE_PYTHON` to its Python
executable. The script rejects interpreters loading the source checkout.

Local evidence is intentionally kept in ignored `.tmp/`, not committed as
runtime logs or fixture repositories. Browser results are machine-readable
in `result.json`, accessibility states in `accessibility.json`, and product
captures are saved beside them. Screenshots are visual evidence, not a claim
of measured device frame rates or formal accessibility certification.

The [original direction](../../design/living-workshop/DIRECTION.md) records
the creator research and proposal. The [Kinetic Precision checkpoint](kinetic-precision.md)
and [functional checkpoint](folio-implementation.md) describe the contracts
preserved beneath this visual revision.
