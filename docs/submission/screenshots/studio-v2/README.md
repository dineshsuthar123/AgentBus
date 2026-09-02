# Studio v2 Execution Observatory Screenshots

These frames were captured by `studio/scripts/capture-submission.mjs` from the
real authenticated loopback Studio. The primary payment run used the
deterministic provider and `mvn -q -o test`; no Azure, Ollama, Razorpay,
provider-network, or payment-network call was made.

The successful run persisted verifier `passed`, reviewer `approved`, and an
offline replay with zero provider calls, zero network calls, and zero process
dispatches. A separate preserved failed run supplies genuine retry and verifier
failure evidence. The approval-only run was explicitly rejected after capture.

| File | Surface |
| --- | --- |
| [01-first-launch.png](01-first-launch.png) | Memory-only bearer connection and product lifecycle. |
| [02-dashboard.png](02-dashboard.png) | Operational command deck and durable run topology. |
| [03-new-task.png](03-new-task.png) | Validated repository boundary and pre-launch execution manifest. |
| [04-payment-demo.png](04-payment-demo.png) | Deterministic payment retry scenario. |
| [05-active-run.png](05-active-run.png) | Active execution approaching the policy boundary. |
| [06-approval-gate.png](06-approval-gate.png) | Exact command, scope, rule, reason, task, and revision. |
| [06-approval-decision.png](06-approval-decision.png) | Capability envelope and explicit decision controls. |
| [07-attempt-topology.png](07-attempt-topology.png) | Successful attempt topology from persisted state. |
| [08-retry-evidence.png](08-retry-evidence.png) | Immutable RetryEvidence and retained mutations. |
| [08-verification-failure.png](08-verification-failure.png) | Authentic failed verifier attempt and diagnostics. |
| [09-verification-passed.png](09-verification-passed.png) | Successful verifier seal. |
| [10-final-review.png](10-final-review.png) | Mandatory final review after verification. |
| [11-source-lens.png](11-source-lens.png) | Source/execution cross-link and bounded Java diff. |
| [11-source-lens-pending.png](11-source-lens-pending.png) | Source inspection while execution remains approval-blocked. |
| [12-evidence-trace.png](12-evidence-trace.png) | Trace, provenance root, and offline integrity verification. |
| [13-offline-replay.png](13-offline-replay.png) | Captured-result replay with no process dispatch. |
| [14-runtime-health.png](14-runtime-health.png) | Sanitized daemon and provider diagnostics. |
| [15-run-history.png](15-run-history.png) | Durable successful and failed run history. |
| [16-runtime-disconnected.png](16-runtime-disconnected.png) | Orientation-preserving disconnected state. |
| [17-dashboard-1280.png](17-dashboard-1280.png) | `1280 x 800` responsive desktop layout. |
| [18-presentation-replay.png](18-presentation-replay.png) | Presentation-only historical replay mode. |
| [19-runtime-events.png](19-runtime-events.png) | Bounded virtualized run event log. |
| [20-reduced-motion-dashboard.png](20-reduced-motion-dashboard.png) | Reduced-motion rendering at `1280 x 800`. |

The harness rejects screenshots containing the bearer token, user-profile path,
or workspace parent path. It never auto-approves: continuation requires
`AGENTBUS_SCREENSHOTS_APPROVE=true`.
