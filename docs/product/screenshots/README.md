# Syndra Studio Screenshots

These images are captured from the real authenticated, numeric-loopback Syndra
Studio. The Payment Safety Demo uses the deterministic provider and an offline
Maven verification command. It makes no live model-provider, payment-provider,
or payment-network call.

The capture harness rejects frames containing bearer tokens, user-profile
paths, workspace parent paths, or stale product/event branding. It never
auto-approves a tool invocation; continuation requires the explicit
`SYNDRA_SCREENSHOTS_APPROVE=true` test-only control.

| File | Surface |
| --- | --- |
| [01-dashboard.png](01-dashboard.png) | Syndra Studio command deck and execution mesh. |
| [02-new-run.png](02-new-run.png) | Validated repository boundary and run manifest. |
| [03-payment-safety-demo.png](03-payment-safety-demo.png) | Concurrent payment retry scenario. |
| [04-active-execution.png](04-active-execution.png) | Active durable execution and task topology. |
| [05-approval-gate.png](05-approval-gate.png) | Exact command, capability envelope, policy reason, and revision. |
| [06-source-lens.png](06-source-lens.png) | Review-relevant file classification and bounded diff. |
| [07-verification-review.png](07-verification-review.png) | Verifier evidence and mandatory final review. |
| [08-integrity-spine.png](08-integrity-spine.png) | Trace manifest, provenance root, and integrity verification. |
| [09-offline-replay.png](09-offline-replay.png) | Captured replay with zero provider, network, and process dispatches. |
| [10-runtime-disconnected.png](10-runtime-disconnected.png) | Safe disconnected state with no persisted session token. |

## Regenerate

Start the authenticated loopback control plane and Vite server as described in
the [payment demo walkthrough](../payment-demo.md). Then run from `studio/`:

```powershell
$env:SYNDRA_STUDIO_TOKEN = "<bearer_token from syndra serve>"
$env:SYNDRA_DEMO_WORKSPACE = (Resolve-Path ..\syndra-payment-demo).Path
$env:SYNDRA_SCREENSHOTS_APPROVE = "true"
npm run screenshots
```

The harness launches and approves a real deterministic run. To capture a
persisted run configured with bounded deterministic latency, also set
`SYNDRA_RUN_ID` to that run's ID. Never commit the bearer token or include it in
command output, screenshots, issue reports, or support bundles.
