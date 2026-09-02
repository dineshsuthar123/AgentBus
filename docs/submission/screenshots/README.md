# AgentBus Studio Submission Screenshots

All images are captured from the real authenticated local Studio at `1440 x
900`, except the explicitly named `1280 x 800` responsive check. The capture
script rejects visible bearer tokens, user-profile paths, and workspace parent
paths.

The current Execution Observatory set is in [`studio-v2/`](studio-v2/). The
legacy submission frames remain in this directory for historical comparison.

## Execution Observatory Highlights

| Category | Screenshot | What it demonstrates |
| --- | --- | --- |
| APPROVAL | [studio-v2/06-approval-gate.png](studio-v2/06-approval-gate.png) | A real server-authoritative gate with the exact offline Maven command, policy rule, and persisted revision. |
| RETRY | [studio-v2/08-retry-evidence.png](studio-v2/08-retry-evidence.png) | Spatial retry topology and immutable retained-change evidence from a real failed run. |
| SOURCE | [studio-v2/11-source-lens.png](studio-v2/11-source-lens.png) | The review-relevant Java file selected ahead of ignored runtime/build paths, with a bounded virtualized diff. |
| REVIEW | [studio-v2/10-final-review.png](studio-v2/10-final-review.png) | Mechanical verifier success followed by the mandatory whole-run reviewer decision. |
| REPLAY | [studio-v2/13-offline-replay.png](studio-v2/13-offline-replay.png) | Captured results reused with zero provider, network, and process dispatches. |

## Best Five

| Category | Screenshot | What it demonstrates |
| --- | --- | --- |
| HERO | [05-active-run.png](05-active-run.png) | A durable payment repair paused at a real policy boundary, with task status, repository impact, and the always-visible Evidence Ribbon. |
| APPROVAL | [06-approval-gate.png](06-approval-gate.png) | The exact Maven tool, command, capability envelope, workspace scope, policy reason, revision, and explicit reject/continue decisions. |
| RETRY/VERIFY | [07-attempt-stack.png](07-attempt-stack.png) | Immutable Attempt 1 failure evidence carried into Attempt 2 without erasing the retained candidate or durable history. |
| DIFF | [11-git-diff.png](11-git-diff.png) | A bounded, readable Git patch where the intended payment source is relevant and runtime/build artifacts remain excluded. |
| EVIDENCE | [13-offline-replay.png](13-offline-replay.png) | Providerless replay of the verified trace with zero provider, network, and historical process dispatches and captured results reused. |

## Complete Capture Set

| File | Surface |
| --- | --- |
| [01-first-launch.png](01-first-launch.png) | Authenticated first launch and AgentBus lifecycle. |
| [02-dashboard.png](02-dashboard.png) | Live Command deck, durable run state, provider routes, and SSE transport. |
| [03-new-task.png](03-new-task.png) | Payment objective, public-safe repository identity, and confirmed Git boundary. |
| [04-payment-demo.png](04-payment-demo.png) | Two retries converging on exactly one payment confirmation. |
| [05-active-run.png](05-active-run.png) | Active durable run and approval-stage evidence. |
| [06-approval-gate.png](06-approval-gate.png) | Exact scoped approval and blocked downstream rail. |
| [07-attempt-stack.png](07-attempt-stack.png) | Persisted retry history and RetryEvidence. |
| [08-verification-failure.png](08-verification-failure.png) | Failed verifier tool invocation followed by the next approval-bound attempt. |
| [09-verification-passed.png](09-verification-passed.png) | Mechanical verification seal and mandatory reviewer decision. |
| [10-final-review.png](10-final-review.png) | Successful whole-run final review after verification. |
| [11-git-diff.png](11-git-diff.png) | Repository-scoped source classification and candidate diff. |
| [12-evidence-trace.png](12-evidence-trace.png) | Trace manifest, provenance root, and integrity verification. |
| [13-offline-replay.png](13-offline-replay.png) | Offline replay counters and trace topology. |
| [14-runtime-health.png](14-runtime-health.png) | Sanitized daemon diagnostics and provider readiness. |
| [15-run-history.png](15-run-history.png) | Persisted successful, failed, and approval-paused runs. |
| [16-runtime-disconnected.png](16-runtime-disconnected.png) | Safe disconnected state with no persisted session token. |
| [17-dashboard-1280.png](17-dashboard-1280.png) | Desktop layout check at `1280 x 800`. |

Regenerate the set with `npm run screenshots` from `studio/` after starting the
local control plane and Vite server. The command requires explicit environment
configuration and never bypasses an approval.
