# Connected purchase-to-access repair

This integration restores an application's missing, currently valid purchased
feature access. It does not interpret arbitrary PRs, issue refunds, compensate
expired periods, change a subscription, or support arbitrary applications without
integration. Imported JSON/CSV identifies candidates, never write authority.

The Syndra daemon stays authenticated and local. The application's correction API
may be remote behind verified HTTPS. There is no public daemon, webhook receiver,
new queue service or hosted execution product in this milestone.

## Install and integrate

```powershell
python -m pip install 'syndra[repair]'
python -m agentbus.repair.integration.reference --help
```

The install includes:

- `agentbus.repair.live.contracts`: frozen, versioned Pydantic authority contracts.
- `agentbus.repair.live.transport.ApplicationConnector`: pinned-destination client.
- `agentbus.repair.integration.server.create_correction_app`: authenticated router.
- `agentbus.repair.integration.postgres.PostgresApplication`: executable reference
  purchase, entitlement, support/refund, receipt, outbox and authorization domain.
- `agentbus/repair/integration/migrations/001_purchase_access.sql`: idempotent
  PostgreSQL migration, including aggregate keys, semantic uniqueness and immutable
  receipt/action triggers. Existing application tables are not discovered/edited.
- `correction-v1.openapi.json`: generated application API, distinct from the local
  control protocol. Regenerate/check with `python -m agentbus.repair.integration.contract`.

An application owner supplies stable purchase/payment/account/product/period
mapping; identity and credential issuance; all normal writer hooks; the domain
transaction; and the scoped normal-authorization check. Email is not a join key.
The reference application is fully implemented, not a universal adapter that
understands an unrelated application's schema.

Mount `create_correction_app(application_domain, principals)` behind the application's
HTTPS ingress. Each `Principal` has an application-issued opaque bearer service
credential and explicit `read`, `approve`, `repair`, `verify`, `revoke` permissions.
Use separate read/verification credentials for analysis-only installations. The
reference process serves one fixed application/tenant/environment identity; it
never selects a tenant from arbitrary request headers.
Use a separate database/schema for each reference application environment. Its
purchase keys are tenant-scoped; this is not a shared-database router for unrelated
applications with overlapping identifiers.

To adapt an existing domain, implement the same typed operations as
`PostgresApplication`: `capabilities`, `read_page`, `purchase`, `register_plan`,
`revoke`, `apply`, `lookup`, `verify`, `access`, and `consume_outbox`. `apply` must
call the application's own access-provisioning domain method, not an administrator
SQL patch. Port its refund/support/cancellation writers to the same aggregate
discipline before advertising `aggregate_serialization=True`. If any required
guarantee is absent, advertise it as false; the controller remains analysis-only.

## Reference consistency contract

The reference uses READ COMMITTED with an explicit `FOR UPDATE` purchase-aggregate
lock. Every provided support, refund, cancellation, restriction, entitlement and
outbox-projection writer takes that lock. It exists before any entitlement, so
missing rows are covered. A payment ID maps uniquely within the tenant. The exact
original period and feature are checked again under the lock.

Target approval rows also lock against revocation. Revocation waits for an already
running transaction and then fences delayed operations and registration through
a retained tombstone. It does not undo a transaction that committed first.

The smallest domain grant, immutable receipt, audit action and outbox insertion
commit in one transaction. A partial unique index admits at most one committed
logical effect for this purchase/payment/account/feature/original period, across
cases and operation IDs. Receipts and action history cannot be updated/deleted
through ordinary SQL; a database administrator can still rewrite storage. Local
hashes are consistency checks, not external signatures or administrator-proof logs.

Only SQLSTATE 40001 and 40P01 restart the entire transaction, at most three times
with bounded jitter. Other SQL errors do not trigger blind mutation retries.
External network calls and notifications are not inside these transactions.
Connections have bounded connect, statement and lock waits and close per operation.

The outbox consumer is idempotent and projects current aggregate state with a
monotonic revision, not stale grant messages. The normal application authorization
path checks that projection, purchased period and current restrictions. Verification
uses that same path with a scoped service capability, not administrator bypass or
real-customer impersonation. A stuck projection remains committed/access pending.
Later legitimate refunds or restrictions produce `superseded`, never another grant.

## External payment boundary

Stripe and the application do not share a database transaction. This reference
does **not** claim to fence a refund performed directly in Stripe's dashboard or
through an unrelated service. It advertises `coordinated_external_payments=False`;
Stripe-authoritative correction plans are therefore analysis-only on this target.

Application-authoritative workflows are writable only when the application owns
all relevant payment/history transitions. For provider coordination, record a
pending payment-change fence under the aggregate lock **before** making the
external call; keep it pending when the provider result is uncertain. Resolve it
through the application's own reconciliation process. An adapter needing stronger
external guarantees must implement and prove them before advertising that capability.
A recent successful Stripe read alone never supplies such a guarantee.

## Business policy

`ConnectedRepairPolicy` (`paid-purchase-access/v1`) is the live, versioned policy;
the old demo/import `RepairPolicy` is unchanged for backward compatibility.
Its canonical hash binds incident `[start, end)`, payment/history/access authorities,
freshness (default 60 seconds), original-period effect, verification and limits.
All instants require a timezone; amounts are integer minor units with exact currency.

Eligibility requires a verified unambiguous mapping, complete authoritative history,
successful paid/captured payment, known missing access, no pending payment change,
no refund/dispute/restriction, and an original period valid now. Period-end
cancellation preserves that valid period; immediate cancellation prevents a grant.
Partial refunds and expired/future periods require investigation. Suspension,
deletion, manual override and changed products are not overwritten. Missing events
are not proof of missing refunds. Already-correct accounts are skipped.

Every candidate has a deterministic disposition, bounded reason, evidence IDs,
revision, proposed period/effect or explicit uncertainty. PR text cannot change
eligibility or execute scripts. The fixed contract, not a model, chooses effects.

## Configure a connection

Use an OS keychain entry, with a prompt rather than a secret on the command line:

```powershell
python -m keyring set syndra.customer-repair my-application
```

Then register a manifest containing **only a reference**:

```json
{
  "connection_id": "my-application",
  "identity": {
    "application": "orders", "tenant": "north", "environment": "staging",
    "configuration_revision": "integration-1"
  },
  "destination": "https://app.example.invalid/syndra-correction/v1",
  "approved_addresses": ["93.184.216.34"],
  "credential_ref": "keyring:my-application",
  "revision": 1,
  "permissions": ["read", "approve", "repair", "verify", "revoke"]
}
```

The address is illustrative. Resolve and explicitly approve your actual destination
addresses. The client validates every DNS result against that set and connects to
an approved numeric address while verifying TLS against the original hostname.
It does not follow redirects or use ambient proxy variables. URL credentials,
queries/fragments, unapproved paths, link-local metadata, mapped IPv6 and default
private/loopback access are rejected. Narrow `private_networks` CIDRs may explicitly
authorize an owned private HTTPS application, not link-local metadata. DNS/IP
rotation requires a reviewed manifest revision. TLS roots come from the OS/Python
trust store; do not disable certificate or hostname verification.

`env:SYNDRA_REPAIR_NAME` is an alternative reference resolved only inside the daemon.
Only the documented keychain/variable names are accepted. Studio cannot create
destinations, choose arbitrary local files or receive secret values. The controller
binds the resolved credential fingerprint internally to approval; it is not a
browser credential or a report field. An `https` connection and verified identity
are necessary, not sufficient, evidence of a production-ready installation.

```powershell
syndra repair connection-add manifest.json --workspace .
syndra repair connection-check my-application --workspace .
syndra repair conformance my-application --purchase PURCHASE_ID --workspace .
```

Conformance reads identity, capabilities and a bounded purchase page without
customer writes. It does not certify undisclosed writer hooks. Run the adversarial
suite against the reference and port its deterministic writer/barrier tests to
your application before authorizing a staging canary.

## Stripe evidence

Install uses official `stripe==15.6.1`, explicitly pinned to its generated API
version `2026-08-26.dahlia` rather than your account's changing default. Configure
`stripe` in the manifest with `credential_ref`, `platform_account`, optional
`connected_account`, `livemode`, `api_version`, and `use_entitlements`.

Only restricted `rk_test_`/`rk_live_` keys are accepted, matching the configured
mode. Grant read access sufficient for Account identity, PaymentIntents, Charges,
Refunds, Disputes and, when used, Invoices/InvoicePayments/Subscriptions/
SubscriptionItems/Active Entitlements. Do not grant write access. The connector
checks platform identity, optional Connect identity and mode on evidence objects;
all scoped reads carry the correct Connect header. Full secret keys are rejected.

Supported configuration is one settled/captured PaymentIntent and, optionally,
one unambiguous paid invoice line, transaction, subscription item, product and
original period. More complex invoices stop for investigation. Refund/dispute,
invoice-payment, line-item, subscription-item and active-entitlement pages are
streamed up to 100 pages of 100 objects. Positive absence claims require complete
pagination; a disqualifying refund/dispute or ambiguous single-item mapping can
stop early without claiming absence. Exceeding the bound, missing pages, duplicates
or schema/mode mismatches fail closed. No event ingestion/order inference
is used. Product-feature mappings are never changed. No Stripe mutation method is
exposed. Provider error bodies are discarded; SDK debug/info body logging is refused.
Each purchase has a 45-second budget for starting further evidence calls, in
addition to each request's bounded transport deadline. Observed rate limits share
an account/Connect/mode/API-version cooldown across clients and credential rotation
within the daemon; Retry-After is not shortened. Transient/authentication failures
also impose a short bounded cooldown. This read cooldown is memory-only, not a
durable provider rate-limit ledger across daemon restarts.
Known Stripe-backed purchases cannot be made eligible by switching to application
authority. This reference does not advertise coordinated external payment writes.

## Review, execution and recovery

Studio: import a case, select a checked connection, optionally load a one-column
`purchase_id` CSV, refresh authoritative evidence, select records, review the exact
plan, approve once, and execute. Default write cap is one; deliberately raise it
before refreshing for a batch (maximum 200). Candidate scans are bounded to 10000,
network pages to 100, Studio decision pages to 50, and mutation concurrency to one
per plan. Mutation rate is bounded to the policy's 1-10 requests/second.

The existing `ToolPolicyEngine` and exact `ToolApprovalGrant` bind plan digest,
workspace, connection/config/credential revisions, target/environment, selected
operations, policy, resource scope, expiry and maximum writes. Runtime StateStore
persists the request/grant, run and attempt alongside the remote-effect journal.
The coding executor refuses this workflow; it cannot reinterpret it as a coding
task. Generic control-plane cancel routes to target-aware emergency stop.

Local immutable intent is saved before dispatch. Execution is `queued`,
`dispatched`, `not_applied`, `committed`, or `outcome_unknown`; verification is
independently not started, pending, passed, failed or superseded. Counts come from
persisted per-operation receipts, not optimistic UI updates. No automatic mutation
retry occurs. Lost responses, generic 5xx and crash windows reconcile the **same**
target receipt. A momentary missing receipt is not evidence that a delayed request
cannot commit. An unknown purchase cannot escape into a new plan/key.

An explicit retry first looks up the old receipt, reuses its immutable operation,
honors a persisted Retry-After/backoff deadline and stops after three dispatches.
Authentication, scope and precondition problems need inspection. No new key is
generated to escape an unknown outcome. Target semantic uniqueness and revoked-plan
tombstones protect against delayed workers; this is not universal exactly-once
delivery. Receipts live indefinitely, beyond any provider idempotency cache.

Pending verification polls at most three times with bounded backoff and a shorter
read timeout; an outage stops a reconciliation pass instead of making hundreds of
unavailable reads. Last observation time/revision and current read errors remain
visible. A later business change or service outage never rewrites a successful
historical runtime attempt.

`Reconcile saved operations` only reads receipts/access, including after expiry,
stop or restart. It never dispatches queued operations. Pause stops new local
dispatch; continue uses the still-valid exact approval. In-flight operations may
finish. Emergency stop also revokes the target plan and cannot be undone; reconcile
it and review genuinely new work. Connection/policy/scan changes invalidate old
approval and fence outstanding plans at the original target. If fencing cannot be
confirmed, the failure remains visible and writes do not silently move to a new
target. Stop/cancel is not rollback.

CLI equivalents include `connected-preview`, `connected-plan`, `connected-approve`,
`connected-execute`, `connected-reconcile`, `connected-show`, `connected-pause`,
`connected-continue`, and `connected-stop`. Their `--help` describes exact fields.
`connected-preview --csv candidates.csv --policy policy.json --connection ID CASE`
rehydrates up to 10000 identifiers. Case imports and exported reports never carry
a usable runtime grant or credentials. Replay never starts a live correction.

## Storage, migration and retention

Old cases and the 65/15 guided SQLite demo remain unchanged. Connected tables are
additive in the existing managed `customer-repair.db`; durable runtime schema stays
7 and the target migration version is 1. Existing attempt history is not rewritten.
Databases and artifacts remain under the resolved `.syndra` state directory or
explicit external managed directory, not ordinary repository changes.

Use pseudonymous IDs. The application owns any lookup back to customer PII. Local
storage is plaintext; protect the OS account, backups, database DSN and exports.
No customer bodies, payment metadata, prompts, secrets or raw HTTP errors enter
logs/reports. Application state retained for eligibility is the bounded typed
contract, not a raw Stripe response.

Scans/plans are capped at 50 per case; connections at display/read bounds of 100.
`connected-purge-evidence CASE --confirm PURGE_UNREFERENCED_EVIDENCE` explicitly
removes only scan evidence not referenced by a
plan. Plans, operation payloads, receipt references and target tombstones remain
because deleting deduplication history weakens retry safety. Archive/encrypt backups
according to your own retention policy. There is no automatic destructive cleanup.

## Operations runbook

1. Back up local managed state and application storage. Apply the packaged target
   migration using an application migration role, then use a non-superuser runtime
   role with only required table/function privileges. Never point the reference
   seed command at production.
2. Validate identity, TLS/pinned addresses, exact permissions, normal writer hooks,
   outbox processing and application authorization. Record your own deployment's
   conformance evidence before a separately authorized staging/live canary.
3. Review the incident interval and authority policy; refresh evidence and inspect
   uncertainty. Do not increase caps to make conflicting evidence eligible.
4. If a response is lost, read/reconcile the saved plan; preserve its operation ID.
   Keep unknown records visible. Do not delete the database to retry a repair.
5. Pause to inspect; emergency stop to fence new target work. Reconcile requests
   already dispatched. If the target is unreachable, stop is locally requested,
   not proof that no earlier transaction can commit.
6. Rotate by issuing a new scoped credential to the same application principal,
   changing the local reference/manifest revision, fencing old plans at their
   original destination, and checking identity again. Revoke the old key at the
   application. If the new key cannot fence old plans, resolve that target-side
   revocation first; do not silently widen permissions or redirect the old plan.
7. Compensation is unsupported. A legitimate refund/restriction uses the normal
   domain action and supersedes the earlier repair. Do not restore historical rows
   or remove later upgrades/support actions in the name of rollback.

## Supported/tested versions

| Component | Contract/range | Evidence |
| --- | --- | --- |
| Python | Existing 3.11-3.14 package support | This milestone locally exercises 3.14.6; existing CI matrix remains. |
| PostgreSQL | 16-18 integration SQL | Final local service tests use 18.6. The configured PostgreSQL 16 CI job has not run remotely. |
| Psycopg | >=3.2,<4 | Locally 3.3.6 binary distribution. |
| Stripe Python | 15.6.1 | Offline official-SDK contract fixtures. |
| Stripe API | 2026-08-26.dahlia | Explicit version headers; no sandbox/live credentials supplied. |
| Keyring | >=25,<26 | Credential-reference integration; no real credential is committed. |
| FastAPI / Pydantic / Uvicorn | Existing supported package ranges | Source tests: 0.139.2 / 2.13.4 / 0.51.0; fresh installed browser flow: 0.162.0 / 2.14.1 / 0.54.0. |
| Control protocol / durable schema | 1.0 / 7 | Additive generated types; old cases remain readable. |
| Target API / target migration | syndra.correction/v1 / 1 | Generated contract, actual PostgreSQL/network tests. |

Optional dependency licenses: Stripe Python MIT; keyring MIT; Psycopg LGPL-3.0
with its documented licensing exceptions and libpq's PostgreSQL license. They are
installed dependencies, not copied source/assets. See each installed distribution's
license metadata and retain notices when redistributing its binaries.

## Primary references

These sources guide the design; their presence does not verify a deployment.

- [PostgreSQL explicit row locking](https://www.postgresql.org/docs/18/explicit-locking.html): the shared aggregate lock discipline.
- [PostgreSQL transaction isolation](https://www.postgresql.org/docs/18/transaction-iso.html): database-local guarantees, not a distributed payment lock.
- [PostgreSQL serialization retries](https://www.postgresql.org/docs/18/mvcc-serialization-failure-handling.html): bounded whole-transaction restart.
- [Psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html): commit/rollback connection boundaries.
- [Stripe SDK pinned API version](https://github.com/stripe/stripe-python/blob/v15.6.1/stripe/_api_version.py): typed client/version alignment.
- [Stripe account scoping](https://docs.stripe.com/connect/authentication): per-request Connect identity.
- [Stripe restricted keys](https://docs.stripe.com/keys): least-privilege read credentials.
- [Stripe refunds](https://docs.stripe.com/api/refunds/list), [disputes](https://docs.stripe.com/api/disputes/list), [invoice payments](https://docs.stripe.com/api/invoice-payment/list), [invoice lines](https://docs.stripe.com/api/invoice-line-item/retrieve): complete authoritative pages rather than embedded summaries.
- [Stripe active entitlements](https://docs.stripe.com/api/entitlements/active-entitlement/list) and [application provisioning](https://docs.stripe.com/billing/entitlements?dashboard-or-api=api): provision in the app, not by changing product-wide mappings.
- [Stripe idempotency](https://docs.stripe.com/api/idempotent_requests): provider cache does not replace retained target receipts.
- [AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html): transactional intent plus idempotent downstream consumption.
- [OWASP SSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html): approved destinations, no redirects, DNS-to-connection binding.
- [OS keyring integration](https://keyring.readthedocs.io/en/latest/): references instead of browser-held production credentials.
