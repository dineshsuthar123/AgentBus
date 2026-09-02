# AgentBus Studio: Judging Summary

## Problem

Models can propose useful code, but letting them act on a real repository turns
quality mistakes into operational risk. Commands can escape scope, approvals
can become vague, retries can erase history, and a passing test can be mistaken
for sufficient review. Teams need control over execution, not another chat UI.

## Product

AgentBus is a safety-oriented local runtime for autonomous software engineering.
AgentBus Studio is its authenticated visual control plane. It makes the durable
task graph, managed tools, exact approval gates, immutable attempts, repository
diff, verification, mandatory final review, trace provenance, and offline replay
visible in one workflow.

## Why AI Matters

Planner, coder, verifier, and reviewer roles turn an open-ended engineering
objective into bounded tasks, repository edits, mechanical checks, and a final
decision. AgentBus supports an offline deterministic provider for reproducible
demos and CI, local Ollama, and explicitly configured Azure OpenAI. Providers
propose work; independently enforced runtime policy controls what can act.

## Why It Is Technically Difficult

The hard part is preserving safety across the whole lifecycle. AgentBus binds
every tool request to a versioned schema, derived capabilities, canonical
workspace, Git repository boundary, cumulative budget, policy decision, and
exact approval revision. SQLite-backed attempts remain immutable across resume,
terminal successful tasks do not rerun, and downstream work cannot silently
rewrite earlier task history.

## Payment Demonstration

For this Razorpay submission scenario, a Java payment service begins with a
real correctness bug: sequential and concurrent duplicate confirmations can
produce more than one successful business transition. The deterministic
AgentBus run validates an isolated Git repository, plans one bounded repair,
changes only `PaymentService.java`, pauses the exact offline `mvn -q -o test`
command for approval, passes verification, and receives mandatory final-review
approval. This is a submission workload, not a Razorpay integration or
endorsement.

## Safety Architecture

Studio talks only to an authenticated numeric-loopback control plane. The
durable task engine separates model roles from a managed tool runtime. Policy,
approval, verifier, and reviewer gates surround repository effects. Canonical
workspace and Git checks prevent accidental parent-repository scope. AgentBus
does not claim complete sandbox isolation and does not automatically roll back
filesystem edits; failed runs report created and modified artifacts instead.

## Evidence And Replay

The flight recorder stores sanitized spans and content-addressed evidence under
a provenance root. Integrity verification detects missing or changed trace
objects. The demonstrated offline replay completed with `0` provider calls, `0`
network calls, `0` historical process dispatches, and captured results reused.
That distinction makes replay useful without pretending historical commands ran
again.

## Business Value

AgentBus gives engineering teams a reviewable boundary between model intent and
repository action. Exact approvals reduce ambiguous operator decisions; durable
history supports incident review; scoped diffs focus human attention; and
providerless replay enables reproducible local evaluation without spending on a
live model call.

## What Works Today

The public beta includes the CLI, React Studio, native VS Code control surface,
deterministic and Ollama routes, optional Azure OpenAI integration, durable
multi-agent execution, workspace-scoped managed tools, approvals, retries,
verification, final review, Git evidence, trace integrity, offline replay,
repository intelligence, and offline acceptance suites on Windows and Linux.

[Architecture diagram](assets/agentbus-architecture.svg) | [2:36 demo script](demo-script.md) | [Selected screenshots](screenshots/README.md)
