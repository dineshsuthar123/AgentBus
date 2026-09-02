# AgentBus Studio v2: Execution Observatory

## Purpose

Studio v2 is an operating surface for repository execution, not a dashboard
skin. Its primary view joins three dimensions that are currently spread across
tabs:

- **Space:** repository, execution, and evidence
- **Time:** plan, execute, approve, verify, review, and replay
- **Control:** automatic progress, exact human approval, and policy block

The control plane remains authoritative. Studio may project, filter, and replay
persisted observations, but it must never invent execution, imply an optimistic
approval, expose hidden model reasoning, or alter safety policy for display
convenience.

## 1. Current Strengths

- The UI consumes generated protocol types and preserves existing control-plane
  contracts.
- The bearer token is held in component memory and is neither displayed after
  connection nor written to browser storage.
- Origin validation limits Studio to credential-free numeric loopback HTTP.
- Workspace validation, approval revision checks, replay counters, bounded
  diffs, reviewer rejection, and provenance are represented truthfully.
- Mutations are pessimistic: approval, resume, cancel, trace verification, and
  replay wait for server responses.
- The deterministic payment scenario exercises real local repository changes,
  exact Maven approval, verification, review, provenance, and offline replay.
- Existing colors already have useful semantics: cyan execution, amber
  approval, emerald success, coral failure, violet evidence/replay.
- Reduced-motion CSS and semantic native controls provide a useful baseline.
- At audit time, 8 Vitest files / 33 tests, lint, typecheck, and build pass. The
  production JS is one 275.68 KB chunk (82.82 KB gzip); CSS is 50.30 KB
  (10.38 KB gzip).

## 2. Current Weaknesses

### Product and information architecture

- Run state is split into six tabs, so approval, active execution, source
  changes, review, and evidence cannot be understood in one spatial context.
- `ExecutionRail` is a five-stage summary, not a graph of persisted tasks,
  attempts, tool invocations, approvals, and evidence transitions.
- Attempts are stacked cards. Retry cause and recovery are not spatially linked
  to the verifier failure that produced persisted `RetryEvidence`.
- Source is a file list beside a full rendered diff. It is not cross-linked to
  attempts, invocations, verifier observations, or timeline position.
- The evidence ribbon reports useful facts but is not interactive and does not
  distinguish live, historical presentation, and offline replay strongly
  enough.
- The dashboard leads with generic metric cards and table rows rather than
  active topology, attention gates, and miniature run graphs.
- There is no stable contextual inspector, event flight recorder, command
  palette, or deep-linked selection model.

### State, networking, and resilience

- `StudioContext` contains connection state, all dashboard snapshots, stream
  status, and a global `eventRevision`. Any accepted SSE event eventually
  refreshes four global endpoints and invalidates every consumer.
- `RunPage` includes that global revision in its effect dependency and reloads
  up to 14 run endpoints after any event, including events for other runs.
- Run loads use an `active` boolean but do not abort requests when navigation
  changes. Optional endpoint failures are silently discarded, so partial and
  unavailable states are indistinguishable.
- GET requests have timeouts but no request deduplication, safe read retry, or
  stale-while-revalidate cache. Mutation and read policies are not explicit in
  the API surface.
- SSE tracks a monotonic cursor and sends `Last-Event-ID`, but reconnect delay is
  constant, stream state is boolean, and there are no jittered bounds,
  reconnect metrics, authoritative reconciliation callback, or bounded event
  history.
- Events are discarded after triggering a refresh. There is no immutable event
  ledger from which a timeline, replay presentation, diagnostics, or scoped
  run subscription can be derived.
- A panel exception can still replace the entire React tree.

### Rendering, scale, and accessibility

- `DiffViewer` splits and mounts every line. A large bounded diff can still
  create thousands of DOM nodes in one render.
- Tool and audit timelines mount every returned record. There is no reusable
  virtual log surface for 50,000 logical records.
- Every page and component is eagerly imported into the initial bundle.
- The single compressed CSS file mixes tokens, shell, pages, components,
  animation, and responsive rules. It is compact but difficult to evolve.
- Keyboard support is primarily native tab order. There is no graph roving
  focus, event navigation, command palette, focus transfer to approval, or
  screen-reader lifecycle announcement.
- Several custom visual structures have labels but no equivalent list or graph
  semantics. Copy actions do not announce success.
- Current responsive behavior becomes a stacked page below 820 px rather than
  collapsing observatory regions according to priority.

## 3. Current Event and Rerender Flow

```text
ControlEventReader (SQLite, redacted, monotonic event_id)
  -> global /api/v1/events SSE
  -> StudioEventStream parses one frame
  -> cursor guard drops sequence <= current cursor
  -> StudioContext schedules a 180 ms timeout
  -> load info + providers + doctor + runs
  -> eventRevision++
  -> all StudioContext consumers rerender
  -> mounted RunPage reloads its full RunBundle
  -> RunPage and all selected-tab children rerender
```

The current debounce prevents the worst event/render storm, but it treats
events only as invalidation signals. It does not batch normalized event state,
scope updates by run, or distinguish ingestion from snapshot reconciliation.

## 4. Event-Volume Bottlenecks

- One global context value changes whenever any snapshot or stream flag changes.
- A global stream event can cause unrelated run endpoint fan-out.
- Multiple overlapping bundle loads are neither deduplicated nor cancelled.
- All diff lines, tool invocations, audit records, attempts, and trace spans are
  rendered eagerly.
- Derivations such as status counts and task/attempt joins run during component
  renders rather than in stable selectors.
- The app retains no bounded normalized event buffer, so adding a timeline
  directly to React state would create a second render storm.
- Hover-driven graph state would be expensive if stored at page/context scope.

## 5. New Information Architecture

The authenticated application uses one stable shell:

```text
Nav rail | Context / command bar                         | Integrity spine
         | Execution mesh              | Inspector       |
         | Source lens (collapsible contextual region)  |
         | Event flight recorder / replay timeline      |
```

Primary routes remain hash-based for backend compatibility:

- `#/` operational command deck: Active, Attention, System Topology, Recent Runs
- `#/new` execution configuration with visible repository/capability/side-effect
  scope before launch
- `#/runs/:runId` observatory with optional `attempt`, `node`, `event`, and
  `view` query parameters
- `#/history`, `#/runtime`, and `#/demo` as focused secondary surfaces

The run observatory is not tab-based. The mesh remains visible while a stable
inspector changes context. Source, evidence, and timeline can collapse without
destroying selection or execution orientation.

## 6. New Interaction Architecture

### Selection and presentation

- Selection is local presentation state: selected node, edge/event, attempt,
  file, inspector view, timeline cursor, and live/manual/replay mode.
- Selecting a mesh node opens the matching inspector and highlights related
  persisted files/events where the protocol contains that relationship.
- Selecting a file opens its bounded diff and highlights task/attempt metadata
  only when persisted links exist. Missing links are labeled unavailable rather
  than inferred.
- Scrubbing backward pauses visual following while ingestion continues. The
  timeline exposes `LIVE +N`; returning live selects the newest accepted event.
- Replay navigation changes only presentation state. Captured results say
  `PROCESS NOT DISPATCHED` whenever the persisted replay counter is zero.

### Approval

- A pending persisted approval inserts an amber gate in the mesh, dims
  downstream nodes, stops active-edge animation, opens the approval inspector,
  announces the pause, and moves focus to its heading.
- Exact command, executable, cwd, capabilities/scopes, policy rule, reason,
  revision, and identifiers come from `ApprovalSummary` only.
- Reject and Approve & Continue disable together while the exact revision is in
  flight. The gate changes only after the server confirms and the authoritative
  run snapshot reconciles.
- No approval action receives a single-key shortcut.

### Keyboard model

- `Ctrl/Cmd+K`: command palette
- `/`: run search
- `J` / `K`: next / previous event
- `[` / `]`: previous / next persisted attempt
- `F`: focus active mesh node
- `D`: source lens
- `E`: evidence inspector
- `Esc`: close contextual layer or return focus to the mesh

Graph nodes use roving focus plus an accessible ordered textual equivalent.
Shortcuts do not fire while typing in editable controls.

## 7. Visual Primitives

- **Execution mesh:** custom semantic SVG with task lanes, stage glyphs,
  selectable transition edges, and a textual fallback. Only a genuinely active
  edge animates.
- **Approval gate:** a physical amber break in topology, not a detached alert.
- **Capability envelope:** nested bounded scopes showing capability, resource,
  and approval state; not a collection of decorative pills.
- **Attempt branch:** failure, persisted retry evidence, and recovery branch
  attached to the owning task.
- **Source lens:** classified file tree, activity markers, bounded virtual diff,
  and execution cross-links.
- **Integrity spine:** compact interactive trace, provenance, mode, provider,
  network, process, captured, and replayability state.
- **Flight recorder:** horizontal event topology with live, manual, and replay
  cursors, backlog count, and textual event list.
- **Context inspector:** one stable region for planner, coder/task, tool,
  approval, verifier, reviewer, file, event, and evidence facts.

Graphite/black-blue surfaces and a restrained topology grid form the base.
State light is semantic, not decoration. Motion tokens are `micro` 120 ms,
`panel` 210 ms, and `topology` 280 ms. Reduced motion removes continuous and
spatial animation while preserving state labels and shape changes.

## 8. Performance Strategy

### Event pipeline

```text
SSE bytes
  -> parser
  -> validate / normalize
  -> monotonic + duplicate guard
  -> bounded immutable EventStore
  -> frame/microtask batch
  -> scoped external-store subscriptions
  -> derived run selectors
  -> presentation
```

- Use `useSyncExternalStore` subscriptions instead of putting event arrays in a
  global React context.
- Keep at most 10,000 normalized event envelopes by default, with counters for
  evictions and duplicate/out-of-order drops.
- Batch a burst into one notification frame; snapshot reconciliation is
  separately debounced and scoped to affected run IDs.
- Keep hover state inside the mesh and avoid state writes for pointer movement.
- Use stable derivation functions for graph, timeline, and source relationships.

### Server state and requests

- Add a small in-repository query cache rather than a new dependency: keyed GET
  deduplication, bounded stale time, stale-while-revalidate, AbortSignal
  composition, timeout, and bounded retry for idempotent reads only.
- Bundle loads accept an AbortSignal and retain endpoint-level partial errors.
- Route/run changes abort obsolete reads. Approval, cancel, resume, trace verify,
  and replay creation are never automatically retried.

### Large surfaces and bundle

- Lazy-load run observatory, source/diff, logs, evidence/replay, and secondary
  routes where practical.
- Use fixed/estimated-row windowing with overscan for 50,000 logical log records
  and large diff lines. Only the visible window mounts.
- Diff normalization is linear and chunkable; large input parsing is moved to a
  worker when browser support is available, with a bounded main-thread fallback.
- Target first operational shell below 200 KB gzip JS. Record first shell and
  largest lazy chunk from the production build.
- Deterministic fixtures measure 10,000-event ingestion, notification/render
  count, duplicate rejection, bounded buffer size, 50,000-record DOM window,
  and large-diff interaction. Numbers are captured from test/build output, not
  hard-coded as product claims.

## 9. Resilience Strategy

Connection state is explicit:

```text
connecting -> connected -> degraded -> reconnecting -> restored -> connected
                              |                |
                              +---- offline ---+
```

- SSE reconnects with exponential backoff, bounded maximum delay, and jitter.
  It sends the last monotonic cursor through both `after` and `Last-Event-ID`.
- Duplicate or older sequences are counted and dropped before storage.
- On reconnect, Studio retains the last snapshot, reconciles authoritative REST
  state, then announces restoration at the accepted cursor.
- Reconnect failure never clears the shell. Runtime-offline mode is read-only
  and offers a deliberate reconnect/refresh action.
- Route and panel error boundaries contain diff/log/evidence failures. The
  execution mesh and a dedicated approval failure surface remain available.
- Sensitive values are not persisted. Local storage contains only validated
  layout density/collapse preferences. URLs contain IDs and view coordinates,
  never bearer tokens or payloads.
- React escaping remains the output boundary; no raw HTML is introduced. ANSI
  control characters and unsafe path display characters are sanitized before
  presentation. A restrictive same-origin CSP is added if compatible with Vite
  and the loopback proxy.

## 10. Accessibility Strategy

- Meet WCAG AA contrast for text, focus rings, and every semantic state.
- Use landmarks for navigation, command bar, mesh, inspector, timeline, and
  integrity spine.
- Every mesh node is a native button or keyboard-operable SVG group with a
  textual list equivalent containing actor, status, task, and attempt.
- Use visible `:focus-visible` treatment and deterministic focus restoration.
- Move focus to a newly persisted approval gate and announce `Approval
  required`; announce run success/failure, reconnecting, and restoration through
  a polite live region without repeating on every refresh.
- Status always combines color with text, icon/shape, or line treatment.
- Command palette uses dialog/listbox semantics and returns focus on close.
- Virtual lists preserve `aria-setsize` / `aria-posinset` and expose search and
  navigation controls outside the scrolling window.
- `prefers-reduced-motion` disables active edge flow, panel transforms, and
  cursor animation. No information depends on animation.
- At 1024 px the inspector and source lens collapse before the execution mesh;
  at 1280, 1440, and 1920 px all observatory regions remain operable.

## Implementation Boundaries

- Do not change backend APIs, policy, approval, verifier, reviewer, trace, or
  replay semantics for Studio.
- Do not add fake runtime nodes or infer relationships absent from persisted
  fields.
- Do not make provider/network calls in tests or demos.
- Do not persist tokens, prompts, unrestricted tool arguments, or sensitive run
  payloads in browser storage.
- Prefer small framework-independent primitives over a default-looking graph or
  dashboard dependency.
