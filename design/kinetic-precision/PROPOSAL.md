# Syndra: Kinetic Precision

**Frontend design proposal and interactive study | 8 October 2026**

## The Recommendation

Build Syndra as an **engineering instrument**, not another AI dashboard.

Its recognizable object is a sculpted S: five machined layers that assemble
into one system. On the website, it is expressive and interactive. In Studio,
it becomes a compact inspection instrument beside a readable execution list.
When someone enters a diff, approval or failure investigation, the sculpture
gets out of the way.

The material palette is warm porcelain, graphite, brushed-metal edges and
signal orange. The feel should be confident, precise and slightly unexpected,
not futuristic wallpaper. The interface must be equally good after the novelty
wears off, during the hundredth approval and the first serious failure.

This is a substantial replacement direction, not a proposal to add a spinning
object to the existing Folio CSS. It also is **not yet a production migration**.
The separate runnable prototype lets us judge the direction without risking
the authenticated Studio or backend safety contracts.

## What Is Wrong With The Current Presentation

The existing implementation has valuable behavior: typed contracts, scoped
events, durable identity, exact approval, real artifacts and installed-product
acceptance. We should retain that foundation.

The frontend's visual weaknesses are different:

- Three successive stylesheet layers, `styles.css`, `observatory.css` and
  `folio.css`, carry incompatible visual assumptions. Overrides are doing work
  that a coherent component system should own.
- Folder tabs, paper spreads, technical inspector boxes and a floating global
  control strip do not form a single recognizable product language.
- Many surfaces rely on repeated rectangles, separators and similar emphasis.
  The visual hierarchy does not make the user's next decision feel important
  enough relative to supporting chrome.
- The logo, color palette, artifact layout and motion do not share a central
  idea. There is little visual expression of what makes Syndra distinctive:
  powerful local execution with inspectable boundaries.
- Good happy-path code is not enough. Dense approvals, mobile navigation,
  disconnected sessions, long paths and incomplete observations need their own
  deliberate designs, not progressively smaller controls.

The solution is a new composition and design system, with the existing runtime
as the authority. Backend quality does not excuse frontend ambiguity.

## Research: What To Learn, Not What To Copy

I inspected six public sites in a real desktop browser: Lusion, Active Theory,
Huly, Resend, Spline and Raycast. I also read primary product/design material
from Linear, Rive, Teenage Engineering and Lusion's Devin project.
Research screenshots and page observations are in `.tmp/kinetic-research/`.
These are a dated observation of public pages, not an audit of authenticated
products, performance or accessibility.

| Reference | Finding | What Syndra should take | What it should avoid |
| --- | --- | --- | --- |
| [Lusion](https://lusion.co/) | The loaded homepage makes material, scale and an interactive rendered scene central to its identity. | One original, physically convincing signature object. Confident composition around it. | Making visitors wait on a heavy scene before they can use the product. |
| [Lusion / Devin](https://lusion.co/projects/devin_ai/) | The case study explicitly combines AI-product explanation, animation, 3D and WebGL. | Explain a complex system through a designed interaction, not generic AI imagery. | Reproducing another coding-agent brand's sculpture, colors or narrative. |
| [Active Theory](https://activetheory.net/) | The rendered entry is a spatial, atmospheric experience with sparse controls. | A consistent camera, material and motion direction; an experience rather than disconnected effects. | Applying cinematic navigation to a code editor or approval form. |
| [Spline](https://spline.design/) | The homepage presents tangible dimensional objects. Its product supports states, events and data-driven interaction. | Objects with understandable state changes and conventional controls around them. | Stock floating blobs, a mandatory hosted embed or inaccessible canvas-only controls. |
| [Rive](https://www.rive.app/use-cases) | Its examples span interactive components, onboarding, loaders and educational graphics. | State-driven motion throughout the product, not just a hero animation. | Installing an animation framework for every small transition or paying a dependency cost before it is justified. |
| [Linear's March 2026 refresh](https://linear.app/now/behind-the-latest-design-refresh) | Its designers explain reducing navigation emphasis and restoring predictable action placement. | A clear attention hierarchy, dependable controls and quiet supporting chrome. | Copying the familiar dark SaaS sidebar, lavender accent or card vocabulary. |
| [Raycast](https://www.raycast.com/) and [its engineering account](https://www.raycast.com/blog/a-technical-deep-dive-into-the-new-raycast) | The brand has a coherent visual signal. The engineering account treats keyboard behavior, flicker, popovers and performance as part of craft. | A command surface that feels immediate, with rigorous focus, selection and dismissal behavior. | Mistaking a premium screenshot for a premium interaction, or importing its native-shell architecture unnecessarily. |
| [Resend](https://resend.com/) and [its design process](https://resend.com/handbook/design/what-is-our-design-process) | The site balances a sculptural object with direct product explanation. Its process emphasizes implementation and polish in real code. | Material quality, restraint, screenshot-based iteration and whole-journey polish. | A black developer landing page with a different version of Resend's cube. |
| [Huly](https://huly.io/) | The public page connects a strong luminous visual motif with visible product surfaces. | A visual bridge between brand promise and the product people actually use. | Assuming its luminous treatment is proof of 3D, or allowing glow to compete with functional text. |
| [Teenage Engineering](https://teenage.engineering/products/op-1) | The product direction combines physical materials, purposeful controls and screen-by-screen refinement. | The feeling of a made instrument: restrained material depth, clear grouping and useful tactile response. | Decorative knobs, faux screws, sound effects or a literal synthesizer UI. |

**My inference from these references:** the best finish comes from a unifying
idea, material quality and controlled interaction. More WebGL, glow or animation
is not independently evidence of better design. For Syndra, the visual idea must
express bounded autonomy and inspectable engineering work.

## Directions Considered

| Direction | Character | Tradeoff |
| --- | --- | --- |
| **Kinetic Precision, recommended** | Porcelain, machined graphite, sculpted S, exact instrument-like controls. | Distinctive on the website; readable and practical in a long working session. Requires careful material and typography refinement. |
| Optical Signal | Translucent optical objects, refracted light, softer spectral color. | Strong launch impact, but risks generic glassmorphism and difficult contrast in dense Studio surfaces. |
| Living Blueprint | Architectural lines, evolving topology, restrained technical motion. | Excellent for systems explanation, but can become another black terminal/HUD and exaggerate graph complexity. |

The recommendation is one coherent direction. We should not combine all three
into a collage of visual trends.

## The Identity System

### One Object, Several Scales

The S monogram is original, with two angular cuts and softened machined edges.
Five extrusions correspond to planning, implementation, human decisions,
verification and final review. The selected inspection layer is orange.

This is a conceptual system model, **not an assertion that every workflow is
five sequential tasks**. Parallel tasks, repeated approvals and retries still
need their actual persisted graph and event history. A selected orange layer is
not a completion signal. Textual status remains authoritative.

The website uses a larger object with gentle pointer response and an explicit
explode/reassemble action. Studio uses a smaller assembly with an equivalent
HTML list. Source, tests and exact operations remain ordinary selectable text.

### Materials And Color

| Role | Light | Dark | Rule |
| --- | --- | --- | --- |
| Canvas | `#F1F1EB` | `#181E1B` | A quiet desk, not pure white or generic navy. |
| Artifact surface | `#FAFBF6` | `#212823` | Broad readable working surfaces. |
| Recess | `#E7E9E2` | `#2A332C` | Inactive selection and supporting detail. |
| Primary ink | `#202520` | `#EEF1E8` | Strong text contrast. |
| Secondary ink | `#60665E` | `#B4BEAF` | Metadata, never deliberately illegible. |
| Brand signal | `#ED4B24` | `#FF704C` | Identity and inspected selection, not success. |
| Action/attention text | `#B53317` | `#FFA183` | Readable accent, distinct from decorative orange. |
| Verified success | `#356341` | `#ABD1A5` | Only genuine successful observations. |
| Failure | `#A43530` | `#FFAD9F` | Paired with a label, reason and appropriate icon. |
| Keyboard focus | `#14618B` | `#A4DBFF` | Obvious, independent of brand or status. |

Materials are subtle: edge highlights, shallow recesses and a restrained shadow.
Use one strong dark decision surface, not a page of glowing glass panels. The
production token system must add explicit stale, disconnected, disabled,
pending, warning, diff and integrity roles in both themes.

### Typography And Geometry

- **Bricolage Grotesque:** identity, short display headings and milestone titles.
  Optical character without turning every label into a logo.
- **IBM Plex Sans:** forms, task descriptions and functional controls.
- **IBM Plex Mono:** source, commands, paths, IDs and precise technical values.
- Production functional text: 14-16 px; metadata: 12-13 px; source: 13-14 px.
  Smaller edition marks or coordinates may be decorative, never essential.
- A 4 px base rhythm, larger 24/32 px group boundaries, broad artifact surfaces,
  4-6 px ordinary corners, and round controls only where their role merits it.
- One consistent SVG weight and optical size. No mix of unrelated icon packs.

The prototype bundles its fonts and licenses locally. It does not depend on a
runtime CDN. [Font provenance](assets/NOTICE.md) is included.

## Website: A Memorable First Encounter

The first screen is a deliberate split between a clear promise and a tangible
system. Large, carefully tracked type leads; the object is the second voice.
The primary action is usable immediately. There is no forced intro, sound,
scroll hijack, invisible hotspot or preloader blocking the call to action.

Selecting an execution layer changes both the object and its short explanation.
Exploding the layers reveals the relationship between autonomy and control.
The action leading into Studio is explicit, not hidden inside the sculpture.

The full production website should extend this into four chapters:

1. **The instrument:** interactive identity and direct product promise.
2. **The work:** a real, permission-safe recorded workflow linked to an actual
   Studio surface. Label recordings and illustrations accurately.
3. **The boundaries:** repository scope, exact approval, local state and final
   review explained through small functional demonstrations.
4. **The handoff:** installation, native VS Code connection, documentation and
   an honest supported-platform matrix. No fake usage numbers or testimonials.

Use cinematic depth here. Do not put a marketing hero in front of a returning
user's active run.

## Studio: A Working Instrument

### Composition

The prototype uses a work-only queue, a broad central artifact and a clearly
attached decision surface. This is not an all-feature navigation sidebar.
Repository, connections, policy and diagnostics are contextual work surfaces;
history is a complete ledger, not a hundred squeezed tabs.

The important orientation is always visible: repository, run, branch, observed
status and next action. The work area offers Execution, Changes, Checks and
Evidence. The actual task plan, dependencies and activity remain reachable.

The assembly is an inspectable overview, not a replacement for the dependency
graph. It disappears from the diff and check views. Approvals sit beside the
artifact they affect. On mobile, a readable run selector replaces miniature
cards, and the pending-decision action jumps directly to its exact request.

### Complete Feature Plan

| Surface | Proposed treatment | Details that must survive | Prototype coverage |
| --- | --- | --- | --- |
| Entry / resume | Restore the actual last valid work; compact first-task setup when empty. | Unknown run, no workspace, unauthenticated and disconnected are distinct. | Separate website/Studio entry only. |
| Task draft | Spacious bounded brief, explicit workspace and workflow, safe configuration inspection. | Preserve text on navigation; unsupported modes rejected before creation; submit only once. | Session draft, no dispatch. |
| Run working set | Work-only queue with stable ordering and full names. | Many runs, attention overflow, cross-workspace identity and terminal history. | Three coherent sample repositories plus mobile selector. |
| Execution / plan | Small assembly, readable task list and expandable actual dependencies. | Task outputs, attempts, blocked reasons, parallel leases and successful immutable history. | Five conceptual layers and sample observations. |
| Changes / source | Large readable diff with local file selection and precise artifact links. | Bounded output, binary/deleted/renamed files, generated exclusions, long paths and virtualization. | Sample Python diffs, file switching and context filter. |
| Human approval | High-contrast attached exact-operation panel; full details on demand. | Arguments, scope, budgets, revision, expiry, stale 409, rejection and server confirmation. Never optimistic approval. | Exact sample inspection, approve/reject and unverified outcome. |
| Verification | Legible result summary and bounded detailed output. | Passed, failed, not run, timeout and cancelled never collapse into one indicator. | Pending, passed and failed sample views. |
| Final review | Distinct reviewer record after implementation and verification. | Summary, issues, required fixes; rejection blocks commit/PR without rewriting completed tasks. | Approved / not-run illustration; review rejection remains a production design case. |
| Activity | Readable observation ledger linked to task and artifact. | Redaction, truncation, order, resume, gaps and live/disconnected distinctions. | Sample activity dialog. |
| Evidence | Provenance-first trace view with contextual inspection. | Integrity is not correctness; captured/simulated/dispatched must be explicit. | Sample scrubber, zero dispatched tools. |
| Replay / comparison | Dedicated inspection mode with a persistent origin label and exact eligibility. | Real replay lifecycle, cancellation, archive validation, checkpoint/fork boundaries. | Proposed, not executable in this study. |
| History | Server-backed work ledger, filters and cursor paging. | Back/Forward, restored filters/pages, incomplete loads and selected-workspace consistency. | Three-record sample dialog. |
| Repository | Contextual explorer, index, symbols, search and impact. | Canonical Git boundary, stale index, unavailable paths and scoped diff collection. | Read-only sample workspace summary. |
| Connections | Setup that separates saved configuration from actual readiness. | Checked time, model compatibility, explicit diagnostic consent and no automatic paid probes. | Explicitly untested sample providers. |
| Tools / MCP / policy | Scoped catalog, capability inspector and validated edits. | Exact tool identity, permissions, budgets, restarts and unsupported platform behavior. | Sample managed-command scope only. |
| Diagnostics / support | Readable doctor results and sanitized support export. | Recovery steps without secrets, raw prompts or unrestricted arguments. | Proposed. |
| Preferences | Theme, density, motion and keyboard help with immediate preview. | Accessible defaults, system preference and non-sensitive persistence only. | Light/dark and motion controls. |
| Native VS Code | Shared identity and matching run/decision vocabulary in native views. | Trust, SecretStorage, same authenticated daemon, deep links and stale decisions. | Unchanged; integration planned. |

Every production surface must have explicit empty, loading, partial, unavailable
and error designs. Completion should be evaluated by this inventory, not by
how attractive the Execution screen looks.

## Motion: Explain, Do Not Distract

| Interaction | Intent | Production target |
| --- | --- | --- |
| First website reveal | Assemble a recognizable instrument. | Brief staged arrival; functional content never waits for it. |
| Pointer response | Make the object feel tangible. | Small bounded camera response; no custom cursor or required drag. |
| Layer selection | Connect an inspected concept to its explanation. | One restrained material change; conventional button remains selected. |
| Layer explosion | Reveal structure. | Damped assembly transition, not a perpetual spinning logo. |
| Artifact switch | Preserve orientation while changing working context. | Immediate content response; short positional transition, no blurry text. |
| Decision confirmation | Make authority visible. | Wait for actual server acceptance; no confetti or premature success. |
| Failure / disconnect | Stabilize attention. | Stop decorative motion; preserve context and explain the next safe step. |
| Reduced motion | Keep all meaning without movement. | Static geometry, immediate state changes, no lost functionality. |

The prototype's renderer caps device-pixel ratio at 1.6 and stops ambient frames
when paused, reduced-motion, offscreen, hidden or without WebGL. Geometry is
reused; resources are released on surface changes. The HTML list is the
accessible source of meaning. These choices follow the concerns described in
[MDN's WebGL guidance](https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API/WebGL_best_practices)
and [W3C's interaction-animation guidance](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html).

## The Small-Detail Contract

These are acceptance criteria, not optional finishing touches:

- Focus rings are visible in both themes. Dialog focus is trapped, Escape
  dismisses and focus returns to the initiating control. No hidden action is
  required to complete an approval.
- Keyboard selection, mouse hover and live execution status are different
  concepts. A colored selected layer never fabricates completed work.
- High-value mobile actions have generous targets. Do not solve crowding with
  unreadable labels; move supporting detail into an accessible disclosure.
- Preserve artifact selection, scroll position, draft, filters and context
  across navigation. Browser Back and Forward behave like product features.
- Long paths wrap or remain inspectable. Ellipsis always has a full accessible
  name or intentional expansion. Code overflow stays inside its focusable pane.
- Empty search is useful. Zero results is explicit. Loading uses stable geometry
  and bounded progress, not fake percentages.
- Disabled controls explain eligibility. A pending mutation cannot be repeated.
  Cancellation is not silently treated as failure or completion.
- Stale approval, expired permission and disconnected stream keep their own
  messages. A reconnect does not silently resubmit an operation.
- Copy, save and download actions announce their actual outcome. Toasts do not
  cover decisions, steal focus or linger into unrelated outcomes.
- Diff signs accompany colors. Final review has separate authority from tests.
  A failed run still exposes file artifacts, with no destructive rollback.
- Tooltips supplement labels rather than replacing them. Touch users are not
  required to hover. System reduced-motion has precedence over ambient effects.
- No providers are tested on page load. Credentials, prompts and raw sensitive
  payloads never become UI decoration or persisted browser state.

## Performance And Quality Bar

Measured source footprint for the current prototype is approximately **231 KiB
raw / 160 KiB gzip-equivalent**, including all three runtime fonts. The Python
preview serves uncompressed files. This is a size measurement, not a claim
about load time, GPU speed or low-end-device performance.

The renderer uses one 72,288-byte mesh buffer, 1,004 triangles per layer and five
draw calls per frame. It has no rendering-library dependency or downloaded
model. The unused original TTF is retained as provenance, not loaded by pages.

Before production migration is considered finished:

1. Measure cold-start and interaction latency on defined representative Windows,
   Linux and macOS machines, including integrated graphics and a low-power mode.
2. Keep 3D outside the critical code/approval bundle. Target at most 200 KiB gzip
   for the optional scene plus fonts; set the actual app budget from the current
   measured production bundle, not from this small prototype.
3. Target under 100 ms acknowledgement for ordinary local navigation and a
   60 fps scene on the reference machine. If the frame budget is missed, lower
   scene resolution or use the static representation, never delay an action.
4. Reuse existing virtual diff/log surfaces; verify 30,000-line diffs and
   50,000-event views without unbounded DOM growth.
5. Verify 320/390/768/1440 px layouts, both themes, actual browser zoom at 200%,
   reduced-motion, keyboard-only and representative screen-reader journeys.
6. Capture a visual/state matrix for all critical workflows: pending approval,
   stale approval, timeout, worker interruption, review rejection, preserved
   files, reconnect, archive errors, empty workspace and unavailable provider.

Automated axe checks are valuable but not proof of complete accessibility
conformance. A headless desktop GPU test is not a substitute for real-device
performance testing.

## What The Demo Actually Delivers

The working demo includes the website and Studio compositions, original real
WebGL geometry, layer inspection/explosion, light/dark appearance, motion
controls, three scoped sample repositories, diffs, checks, evidence inspection,
exact sample approval/rejection, history, activity, workspace inspection,
keyboard command search and a preserved session draft.

It contains no API client or credential storage. A content security policy
blocks network connections. It cannot execute tests, start a run, approve a real
operation, calculate real integrity, export an actual trace, commit or publish.
Illustrative code is not a validated implementation patch. Production Studio
and its authenticated control-plane behavior remain unchanged.

Verification for this study:

- Six Node tests: geometry, triangulation, mesh normals, matrices, projection
  and coherent per-repository fixtures.
- 29 captured browser states: zero automated WCAG-tagged axe violations, page
  overflow or JavaScript exceptions. WebGL is checked for API errors.
- Interactive assertions for exact scope, separate approval/verification,
  rejection, preserved-file reporting, focus return, command navigation, draft
  restoration, evidence selection, scoped artifacts and mobile run selection.
- Reduced-motion freeze, resumed/paused ambient frames and forced WebGL-context
  loss with a usable fallback.
- Zero external requests from the demo browser.
- Existing production Studio: 32 Vitest files / 122 tests passed.

Reports and screenshots are in `.tmp/kinetic-evidence/`. No full Python suite was
rerun for this isolated visual study; no backend implementation was changed.
Actual screen-reader testing, native browser zoom and cross-device frame rates
remain production acceptance work.

## Safe Implementation Order

1. **Approve the direction:** evaluate the website, the approval view and the
   failed/checks view, not just the hero. Decide on the material identity before
   replacing production composition.
2. **Build foundations:** introduce one semantic token/component system in
   React, font licenses and original mark. Avoid another stylesheet appended
   over three incompatible predecessors.
3. **Migrate the critical workflow:** shell, draft, execution, changes, exact
   approval, verification and final review. Reuse generated protocol, typed
   client, event-store semantics and the existing navigation-memory contracts.
   No fixture state or prototype HTML renderer goes into production.
4. **Finish the feature inventory:** real history/paging, repository work,
   readiness/setup, tools/policy, evidence/replay/compare and diagnostics. Keep
   existing deep links and native extension integration compatible.
5. **Add bounded motion:** keep the scene optional, use backend-derived
   observations, test loss/fallback paths and never couple animation to authority.
6. **Earn the finish:** full frontend/backend suites, offline acceptance,
   installed wheel/VSIX verification, real browser matrix, performance and
   manual accessibility review. Verify distribution manifests and all licenses.

The end result should feel exceptional because every detail expresses the same
product idea: **ambitious execution, exact boundaries, visible evidence**.
The prototype is the first proof of that direction, not a claim that the entire
production frontend has already been redesigned.
