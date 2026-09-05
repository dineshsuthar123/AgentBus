# Syndra Studio control-plane audit

Syndra Studio is a presentation layer over the existing authenticated local
control plane. It does not own run, policy, approval, repository, or replay
semantics.

## Existing endpoints reused

| Product need | Existing control API |
| --- | --- |
| Runtime identity and health | `GET /health`, `GET /api/v1/info`, `GET /api/v1/doctor` |
| Provider state | `GET /api/v1/providers` |
| Repository selection | `POST /api/v1/workspaces/validate` |
| Launch and lifecycle | `POST /api/v1/runs`, `POST /api/v1/runs/{id}/resume`, `POST /api/v1/runs/{id}/cancel` |
| Run history and graph | `GET /api/v1/runs`, `GET /api/v1/runs/{id}`, `GET /api/v1/runs/{id}/tasks` |
| Tool activity | `GET /api/v1/runs/{id}/tool-invocations`, `GET /api/v1/runs/{id}/tool-audit` |
| Exact approvals | `GET /api/v1/runs/{id}/approvals` and the revision-bound approve/reject endpoints |
| Verification and review | `GET /api/v1/runs/{id}/report` plus verifier/reviewer trace spans |
| Source activity | `GET /api/v1/runs/{id}/changes`, `/diff`, and bounded before/after file content |
| Trace and evidence | `GET /api/v1/runs/{id}/trace`, `/trace/spans`, `/provenance`, and `/replayability` |
| Offline replay | `POST /api/v1/runs/{id}/replays`, `GET /api/v1/replays/{id}` |
| Scheduler and usage | `GET /api/v1/runs/{id}/scheduler`, `/usage`, and `/worktrees` |

## Event stream

Studio uses the authenticated SSE streams at `GET /api/v1/events` and
`GET /api/v1/runs/{id}/events`. The stream supports `Last-Event-ID`; Studio
keeps a monotonic cursor, ignores duplicate sequences, reconnects with a
bounded delay, and refreshes authoritative resources after an event.

## Fields already available

- Runs expose task text, workspace, workflow, durable status, verifier and
  reviewer status, changed files, failure reason, and cancellation state.
- Tasks expose dependency edges, role, risk, attempts, provider/model route,
  verifier/task-review results, and bounded failure diagnostics.
- Approvals expose revision, kind, requested action, exact executable and
  working directory, capabilities and constraints, affected paths, policy
  rule, resource budget, and expiry.
- Reports expose graph progress, attempt counts, verifier/final-review state,
  reviewer issues and required fixes, changed/generated/commit-eligible file
  classifications, task failures, retained side effects, and cleanup advice.
- Changes and diffs are repository-scoped and bounded by the control service.
- Trace, provenance, replayability, and replay sessions expose integrity IDs,
  checkpoints, span outcomes, captured-result reuse, policy validation, and
  provider/network/process counters without prompts or credentials.

## Minimal additions

Two bounded read operations are justified for Studio rather than inventing an
aggregate dashboard API:

1. A sanitized attempt-history endpoint so RetryEvidence and immutable attempt
   branches can be shown without exposing raw attempt metadata.
2. A trace-verification endpoint that invokes the existing providerless
   `TraceReplayService.verify` operation and returns only integrity metadata.

All other Studio screens compose the existing resources. Browser development
uses a same-origin Vite proxy to preserve the control plane's intentional
no-CORS policy. The bearer token is supplied from the one-time `--json-ready`
handshake and remains in browser memory only.
