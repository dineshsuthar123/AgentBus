# Customer repair

Syndra's first customer-repair workflow answers a specific question: after the
purchase handler was fixed, which paid accounts are still missing access?

Open Studio and choose **Customer repair**. The command palette also exposes
**Repair customer impact**. The feature works locally without a model account.

## Working application demo

Choose **Try the working demo** to create an owned SQLite application with 100
synthetic purchase records. Syndra exercises a broken purchase handler and its
fixed version against actual purchase/access tables. The fixed handler grants
access to a new canary purchase; 65 incident purchases still need correction.
The canaries are outside the 100-record incident cohort.

Choose **Find affected records**, inspect the map or searchable record list, and
review the correction selection. The map supports arrow keys, Home, and End.
The list exposes every record, including those beyond the map's first 100.
Select a square and choose **Read demo account** to read the application's actual
access state. An eligible account reads as locked before correction and unlocked
afterward. The timestamp distinguishes the last read from later changes. Imported
snapshots cannot use this live demo-account endpoint.

The initial cohort contains 65 eligible records, 15 already-correct records,
10 refunded/cancelled records, and 10 unresolved records. Under **Try a change
after preview**, **Support fixes five accounts** changes five real local access
rows. Applying the original 65-record selection then restores 60, skips those
five, and leaves the refunded/cancelled and unresolved records untouched.

**Refund the first purchase** demonstrates a payment change after preview.
Applying re-reads its current payment state and does not restore that access.
These are two separate exercises; using both produces 59 restorations and six
skipped records.

Results report a batch's selected records, not complete incident resolution.
Choose **Preview current records** to see remaining work. Unknown evidence stays
visible after successful corrections. A case survives browser and daemon restarts.

## Imported evidence

Download the JSON template, replace its synthetic facts, and import a snapshot.
Both a bare snapshot and a single `snapshot` wrapper are accepted by Studio.
The CLI accepts the same formats. Imports are limited to 750 KB and 500 records.
The HTTP service also enforces its existing request-body bound.

The supported format is `syndra.purchase-access/v1`. It requires:

- A short title and timezone-aware incident start, end, and observation time.
- Distinct before/after fix revisions and an explicit review confirmation.
- An explicit statement of whether the supplied cohort is complete.
- Stable purchase, payment, tenant, account, entitlement, and event identifiers.
- Payment state, current access state, verified mapping, history completeness,
  and a positive evidence revision for each purchase.

Use pseudonymous IDs. The format excludes names, emails, credentials, free-form
payloads, prompts, SQL, and scripts. Unknown fields and invalid boolean values
are rejected. Data remains plaintext in local managed storage; the product does
not claim database encryption. Protect the local account and any exported files.

The incident interval includes its start and excludes its end. The observation
must follow the incident. Duplicate events within a purchase are normalized;
duplicate purchase rows are rejected. Reused payment/event identifiers across
different purchase rows and contradictory observations for the same entitlement
require investigation. Tenant identity is always part of the access target.

The fixed policy restores missing access only for a verified paid purchase with
complete later history and a known access state. It does not revoke access, infer
an account from an email, change a subscription, issue a refund, or charge a card.
Refunds and cancellations do not receive restored access under this policy.
Products with different eligibility rules need a separately supported contract.

Import review, mapping, and completeness flags are declarations by the exporter;
Syndra does not authenticate those claims or infer causal impact from an arbitrary
code diff. The supported business rule determines the proposed corrections.
Counts cover supplied records. Partial evidence is labelled explicitly.

Imports support analysis and export. There is no live application adapter in this
release, so imported records cannot be applied from Studio or the CLI. The demo
is the only writable adapter. A production integration needs its own identity,
eligibility, current-state, idempotency, and read-after-write contracts.

## Review and results

Previews are immutable, include the exact evidence fingerprint, and expire after
30 minutes. Each batch contains 1-200 distinct selected proposals. A new selection
clears the review checkbox. Applying rechecks current facts; changed evidence that
still appears eligible requires another preview. Already-correct, refunded, or
unknown cases are not blindly overwritten.

The local adapter commits account changes and their saved batch together in one
SQLite transaction. An internal failure rolls back that transaction. This limited
atomicity does not provide rollback for general Syndra filesystem changes or
external applications. No reset, clean, or automatic destructive rollback occurs.

Each apply has a stable request ID. Repeating the exact request returns its saved
result; reusing the ID with different input is rejected. Studio never automatically
retries an apply. If a response is interrupted, **Check saved result** reads that
request. Its identifier, without records or credentials, survives a page reload in
session storage. Alternatively, a new preview reconciles current application state.

Correction packages contain the exact preview basis and batches for that preview.
Other previews' batches remain in case history. The checksum and verification tool
check package consistency; they do not authenticate its author, source truth, or
real-world execution. A package with only proposals is not a completed repair.

## Storage and bounds

Storage resolves from `AgentBusConfig.state_directory_path` to
`<state-directory>/customer-repair/customer-repair.db`, normally under `.syndra`.
An explicitly configured external state directory is respected. The feature does
not write customer logs, databases, or artifacts into ordinary repository paths.
Browser downloads go to the user's chosen download destination.

The separate database has schema version 1. It uses foreign keys, WAL, bounded
writer waits, consistent read transactions, parameterized SQL, and per-operation connections. It does not change
the durable execution database's schema or rewrite existing attempts. Busy/error
responses omit raw SQL, paths, and database contents.

Limits are 200 local cases, 50 previews per case, 100 batches per case, and 200
selected records per batch. Case history is paged in sets of at most 50; activity
shows the last 200 events. Limit errors preserve prior data and explain how to
export it. At the case limit, select a separate managed state directory for new
work. Repeated demo actions with no state change do not append more events.
There is no automatic case deletion. Large files should be partitioned
into meaningful incident cohorts, preserving tenant and identifier uniqueness.

## CLI

```powershell
.venv\Scripts\python.exe -m syndra repair demo --workspace . --json
.venv\Scripts\python.exe -m syndra repair cases --workspace . --json
.venv\Scripts\python.exe -m syndra repair show CASE_ID --workspace . --json
.venv\Scripts\python.exe -m syndra repair preview CASE_ID --workspace . --json
.venv\Scripts\python.exe -m syndra repair import snapshot.json --workspace . --json
.venv\Scripts\python.exe -m syndra repair export CASE_ID --preview-id PREVIEW_ID --workspace . --json
.venv\Scripts\python.exe -m syndra repair verify package.json --json
```

`repair apply --help` describes the exact preview fingerprint, request ID,
repeatable `--purchase` selection, and `--confirm APPLY_SELECTED_CORRECTIONS`.
`repair result CASE_ID REQUEST_ID` retrieves an interrupted request's saved result.
An imported snapshot remains read-only even when confirmation is supplied.
Commands use the normal configuration resolver, including discovered workspace
settings, environment values, `--config`, and an explicit `--state-dir` override.

## Verification

The backend tests cover the 100-record cohort, refunds, missing facts, duplicate
events, tenant boundaries, selection/fingerprint tampering, expiry, concurrent
clients, restart, atomic failure, and repeated requests. Authenticated API tests
include a failure after commit followed by saved-result lookup.

Studio tests cover review/selection, read-only imports, expired previews, keyboard
inspection, and response ambiguity. `studio/scripts/customer-repair-smoke.mjs`
exercises the packaged frontend against the real local daemon, uses an independently
specified expected cohort, and checks accessibility, narrow screens, export,
interruption/reload, refunds, actual application results, and Git cleanliness.

The demo and its tests use synthetic data and no live model/payment service.
Customer interviews, real incident pilots, independent competitor benchmarks,
arbitrary fix interpretation, and production adapters remain separate validation
work described in the product research memo.
