# Syndra / Kinetic Precision

An independent interactive **design proposal**, not the production Studio.
All runs, code changes, approvals and outcomes are clearly labelled samples.
No model, command, filesystem operation, Git action or daemon API executes.
The page's content security policy denies network connections. It needs no
credentials, API key, npm install, third-party CDN or backend service.

## Open The Demo

From the repository root, in PowerShell:

```powershell
.venv\Scripts\python.exe -m http.server 8910 --bind 127.0.0.1 --directory design/kinetic-precision
```

- Website: <http://127.0.0.1:8910/#experience>
- Studio: <http://127.0.0.1:8910/#studio>
- Full design proposal: [PROPOSAL.md](PROPOSAL.md)

Serve **this directory only**, not the repository root. Stop the foreground
preview with Ctrl+C. It is a loopback-only preview, not a deployment server.

## Try It

1. Select the five execution layers and explode/reassemble the real 3D object.
2. Enter Studio. Inspect execution, changes, verification and evidence.
3. Inspect an exact sample approval. Approve or reject it; neither executes
   anything, and approval does not manufacture verification or review success.
4. Explore the explicit approval, verified and failed sample outcomes.
5. Switch repositories. Each sample has its own files and failure observations.
6. Use Ctrl+K / Cmd+K, arrow keys, Enter and Escape. Open workspace, history,
   activity, rationale and a draft. Closing a dialog returns focus.
7. Switch light/dark appearance, pause motion and resize to mobile. The pending
   decision link takes you straight to the approval on narrow screens.

The WebGL object is original procedural geometry, not a stock GLB, embedded
Spline scene, image or video. Keyboard-accessible HTML controls carry all
meaning. Reduced-motion, hidden-tab, offscreen and lost-WebGL paths are handled.
The object has no execution authority. SVG layers remain visible without WebGL.

## Verification

With the existing Studio development dependencies available:

```powershell
node --test design/kinetic-precision/geometry.test.mjs
node design/kinetic-precision/verify.mjs
```

Browser verification expects the preview on port 8910 and an installed Chrome.
Set `SYNDRA_DESIGN_URL` to override the loopback preview URL. Evidence is emitted
under `.tmp/kinetic-evidence/`, outside the prototype and production assets.
`result.json` explicitly records success/failure, captured states, accessibility
violations, overflow, browser exceptions and external requests.

This prototype is deliberately independent of `studio/src`, authenticated
runtime clients, Python distribution assets and backend safety machinery.
Do not transplant its fixture store or HTML rendering into the production app.
Use the approved proposal to implement React components on the existing typed
client, event store and server-authoritative approval state.

## Assets

- Bricolage Grotesque: locally bundled official Google Fonts WOFF2, with its
  original full variable TTF retained as a source asset. SIL Open Font License.
- IBM Plex Sans / Mono: local Fontsource assets, with their bundled licenses.
- Monogram, geometry, shaders and simple SVG controls: authored for this study.

See [assets/NOTICE.md](assets/NOTICE.md) for provenance. Reference-company
screenshots are research evidence only; no company artwork is used in the demo.
