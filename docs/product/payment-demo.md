# Payment Safety Demo Walkthrough

## Recording Preparation

Use a fresh disposable fixture name for each take. Maven dependencies must
already be cached because the approved command is intentionally offline.

```powershell
# Repository root, terminal 1
.venv\Scripts\syndra.exe demo create payment `
  --output syndra-payment-demo-recording `
  --git `
  --json

$demo = (Resolve-Path .\syndra-payment-demo-recording).Path
.venv\Scripts\syndra.exe serve `
  --host 127.0.0.1 `
  --port 8765 `
  --json-ready
```

Verify Maven separately with `mvn -version`. Do not warm the dependency cache
during the recorded run.

```powershell
# Repository root, terminal 2
Set-Location studio
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`, enter the daemon's one-time bearer token, and keep
the canonical `$demo` path ready. Never show the token or an absolute path in
the recording. Before recording, confirm the fixture is broken with `mvn -q -o
test` inside the fixture; the expected failure is behavioral, not dependency
resolution.

## Screen-By-Screen Script

**0:00-0:12 | First launch**

Say: "Models can write code. The harder problem is safely letting them act on
real repositories. Syndra turns that action into a controlled, inspectable
workflow."

Action: Connect to the authenticated local daemon. Keep the token out of frame.

**0:12-0:25 | Command deck**

Say: "Studio is the visual control plane: durable state, policy enforcement,
provider routing, and a live event stream from one local runtime."

Action: Point to Runtime pulse, the deterministic route, and Event stream live.

**0:25-0:40 | Payment safety demo**

Say: "This payment correctness fixture shows how two retries can produce two
successful confirmations. The safe result is exactly one business transition."

Action: Open **Payment Safety Demo** and point from Retry A and Retry B to one
confirmation. Describe it as a generic local fixture, not a payment-provider
integration.

**0:40-0:55 | Repository boundary and launch**

Say: "The repository is canonicalized and checked as its own Git boundary
before any agent receives tools. This run uses the deterministic local route."

Action: Choose **Configure the live run**, enter `$demo`, validate it, and
launch. Keep commit and pull-request toggles off.

**0:55-1:12 | Plan and execution**

Say: "The planner persists a bounded task and done criteria. The coder can edit
only through versioned managed tools, while Source Pulse records repository
impact."

Action: Show the execution rail, task graph, then briefly open **Source**.

**1:12-1:30 | Exact approval**

Say: "Maven is outside the standard executable allowlist, so execution stops.
This decision is exact: tool, executable, offline command, capability envelope,
workspace scope, policy reason, and revision."

Action: Read `test.execute`, `mvn -q -o test`, `test.execute`, and
`process.execute`. Enter a short decision note and select **Approve & continue**.
Mention that **Reject** prevents continuation; there is no allow-forever path.

**1:30-1:48 | Verification and recovery**

Say: "Repository tests are a mechanical gate. A failed attempt remains
immutable, and only persisted RetryEvidence can feed a corrective attempt."

Action: Show **Tools** and **Attempts**. If this fresh run succeeds in one
attempt, do not claim it retried; use authentic preserved retry evidence only
when explaining recovery semantics.

**1:48-2:03 | Mandatory review**

Say: "Passing tests is not completion. Syndra still requires a final
whole-run reviewer, and rejection blocks commit or pull-request creation."

Action: Open **Review** and point to **Verified** and **Approved**.

**2:03-2:16 | Bounded Git diff**

Say: "The candidate changed one intended payment source file. Runtime storage
and build output remain classified outside the commit-eligible patch."

Action: Open **Source** and show the atomic set-based transition in the diff.

**2:16-2:28 | Trace and provenance**

Say: "The flight recorder seals spans, policy decisions, repository evidence,
and replay inputs under a provenance root."

Action: Open **Evidence**, select **Verify sealed trace**, and point to
**Integrity verified**.

**2:28-2:36 | Providerless replay and close**

Say: "Offline replay reuses captured deterministic results: zero provider
calls, zero network calls, and zero historical process dispatches. Syndra
makes autonomous software engineering inspectable, recoverable, and safe to
run."

Action: Select **Run offline replay** and hold on the four counters.

## Recording Guardrails

- Record the real Studio and control plane, not mocked frontend state.
- Do not show tokens, usernames, credentials, or machine-specific parent paths.
- Do not call Azure, Ollama, a payment network, or any paid provider.
- Do not say Maven reran during replay when `Process dispatches` is `0`.
- Do not claim automatic rollback: failed runs may leave reported file edits.
- Use a new fixture directory or remove the prior disposable fixture manually
  before another take; the demo creator intentionally refuses unsafe overwrite.
