# Production customer repair implementation

Baseline: `3a36800` (2026-10-26). This checklist records implementation evidence,
not a certification of a customer's production deployment.

## Capability map at baseline

| Contract | Current implementation | Gap |
| --- | --- | --- |
| Customer cases and demo | `agentbus/repair/{models,analysis,service,demo}.py` | Imports cannot write; SQLite demo is the only adapter. Actual fixture is 65 eligible / 15 correct / 10 ineligible / 10 unresolved, not 60/20. |
| Local authenticated API | `agentbus/control/{app,authentication,repair}.py` | Add connection/plan/operation endpoints under the same guards. |
| Exact tool approvals | `agentbus/policy/{approvals,engine}.py`, `agentbus/tools/protocol/` | Bind a production correction plan and validate the existing grant contract. |
| Durable runtime | `agentbus/execution/{state_store,schema}.py` | Persist remote intents and reconcile independent target receipts. |
| Credentials | Environment-based provider configuration; VS Code SecretStorage for daemon sessions | No reusable production connector credential vault exists at this baseline. Add OS-keychain references; never accept secrets in Studio. |
| Protocol | `agentbus/control/protocol.py`, `protocol/`, generated extension types | Generate additive contracts from Python. |
| Studio | `studio/src/pages/RepairPage.tsx`, `studio/src/api/client.ts` | Add authoritative connected workflow without changing the guided demo. |

## Checkpoints

- [x] Inspect current implementation, approval contracts and primary documentation.
- [x] Versioned authority, connection, conditional operation and verification contracts.
- [x] PostgreSQL application-owned transaction, receipts, semantic uniqueness and outbox.
- [x] Installable integration, migrations, reference service and conformance command.
- [x] Pinned-destination connector and bounded Stripe SDK evidence reads.
- [x] Exact approval, durable intent, recovery, stop and independent access verification.
- [x] Integrated Studio and CLI, generated bindings, packaging and runbooks.
- [x] Real PostgreSQL/network fault scenarios, scale and fresh-artifact installation.
- [x] Existing regression suites and factual acceptance report.

Verified: full Python 2150 passed / 11 platform skips; post-audit packaging/security
17 passed; Studio 203 passed; extension 104 passed; offline evaluation 9/9; installed
wheel/separate PostgreSQL service recovery and five browser/accessibility states
passed. Exact commands, snapshot boundaries and external blockers are recorded in
[the acceptance report](production-repair-acceptance.md).

No production credentials or staging endpoint have been supplied. No live customer
mutation, paid provider call, deployment, release or push is authorized by this task.
