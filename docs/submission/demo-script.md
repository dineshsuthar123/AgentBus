# 2-3 Minute Demo Script

## Preparation

```powershell
.venv\Scripts\agentbus.exe demo create payment --output agentbus-payment-demo --git --json
.venv\Scripts\agentbus.exe serve --host 127.0.0.1 --port 8765 --json-ready
```

In a second terminal run `cd studio`, `npm ci`, and `npm run dev`. Open
`http://127.0.0.1:5173`, paste the one-time token, and keep the absolute payment
demo path ready. Maven dependencies must already be available locally because
the managed command is intentionally `mvn -q -o test`.

## Script

**0:00-0:15**

"Models can write code. The harder problem is safely letting them act on real
repositories. AgentBus is the execution runtime; Studio is its flight deck."

**0:15-0:30**

Open the Command deck. Point to real daemon health, provider routes, durable run
history, and the live SSE indicator.

**0:30-0:45**

Open **Payment demo**. Explain that two concurrent retries must produce exactly
one confirmation. Do not imply Razorpay uses or endorses AgentBus.

**0:45-1:00**

Choose **Configure the live run**, enter the absolute fixture path, validate the
Git boundary, and launch the deterministic multi-agent run.

**1:00-1:20**

Show the planner's persisted task and done criteria. Open Source to show the
real `PaymentService.java` patch and bounded Git diff.

**1:20-1:35**

The Maven invocation stops at the amber Approval Gate. Show executable,
working directory, policy rule, capabilities, scope, approval ID, and revision.
Approve the exact invocation. Mention that reject would prevent continuation.

**1:35-1:55**

Show the managed-tool timeline, verifier seal, and final reviewer. If an
attempt fails, open Attempts and explain the retained immutable failure and
RetryEvidence feeding the next attempt.

**1:55-2:20**

Open Evidence. Verify the trace, point to the provenance root and object count,
then start an offline replay. Read the displayed provider, network, process, and
captured-result counters literally. Do not say Maven reran unless a process was
actually dispatched.

**2:20-2:40**

Return to Source and Review. Close with: "AgentBus makes autonomous software
engineering inspectable, recoverable, and safe to run."
