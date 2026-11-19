# Syndra: 20-persona customer-repair assessment

Date: 2026-10-28. Scope: current working tree, including the uncommitted connected
customer-repair implementation, global onboarding, connection setup and results.

This is a source-grounded, simulated task assessment, not interviews with 20
people. Reactions below are hypotheses. The scenarios are deliberately varied,
not a representative market sample; do not calculate adoption percentages from
them. An unsupported task does not mean its entire industry is unsupported.
Syndra's separate general coding runtime is not being evaluated as a replacement
for these production-domain operations.

## Findings, ordered by impact

### F1. High: the promise and first-run path describe different products

The README leads with finishing work for affected people, then introduces an
agent execution runtime. Its quickstart and Studio's first-run guide start with
a calculator repository. A person arriving with an already-fixed production
incident is asked to learn code-generation concepts before reaching customer
repair. New task remains a primary action on the captured repair screen.

Evidence: `README.md:3`, `README.md:14`, `README.md:48`,
`studio/src/components/FirstRunWelcome.tsx:19`,
`studio/src/components/FolioShell.tsx:88`.

Likely effect: uncertainty about whether Syndra fixes code, repairs customer
state, or is a general agent dashboard. The existing explanation burden falls
on the founder or an engineer rather than the product.

Change indicated: a clear entry choice for an already-fixed incident, a prepared
connection, or a synthetic example. Put the supported purchase-to-access scope
and what cannot be repaired before setup, not after a failed plan.

### F2. High: identifying the affected cohort remains the company's job

Connected refresh checks supplied purchase IDs. It does not infer a complete
affected population from a merged PR, production logs or the incident dates.
The first connected case still starts through a rich JSON snapshot, including
fix revisions and purchase facts. The one-column CSV for up to 10000 candidates
is available inside that case, not as a standalone incident entry point.

Evidence: `studio/src/pages/RepairPage.tsx:81`,
`agentbus/repair/models.py:33`, `agentbus/repair/models.py:93`,
`agentbus/repair/live/service.py:164`,
`studio/src/components/ConnectedRepair.tsx:66`.

Likely effect: an operator who already has a CSV cannot simply begin. A user who
expects Syndra to discover everyone affected may stop before receiving value.

Change indicated: incident details plus a checked connection and candidate CSV
should form a first-class entry. Rehydrate facts at the server. State clearly
whether cohort coverage is supplied, partial or unknown. Do not claim automatic
causality or population discovery, and do not make imported IDs write authority.

### F3. High: production setup is a substantial developer integration

The owner must map domain identities, supply the transaction and normal-access
check, coordinate relevant ordinary writers, deploy a protected application API,
issue scoped credentials and configure pinned destinations. The documented domain
surface contains eleven operations. This is not equivalent to adding a URL.
The supplied server library is Python; another stack must implement the contract
or an appropriate application-owned integration, not merely install a Python
package into a Java/Node/.NET application and expect automatic domain mapping.

Connections currently offers explanatory text and a CLI manifest-registration
command, not a guided setup/readiness workflow. Its documentation reference is
rendered as a code path. There is no browser-side destination registration, which
is an intentional security boundary rather than a defect to remove.
The README's first install uses the `ide` extra, while the repair dependencies
are a separate extra. Setup needs to distinguish the local client's Stripe/keychain
requirements from installation of the target's Python server integration; installing
only in the application service does not install dependencies in the local daemon.

Evidence: `docs/product/production-customer-repair.md:32`,
`docs/product/production-customer-repair.md:48`,
`studio/src/components/RepairConnections.tsx:27`.

Likely effect: first-incident users face a second engineering project during an
outage. For a small, one-off repair, integrating Syndra may cost more work than
the company's existing reviewed support/repair path. Repeated incidents at an
already-integrated application are a materially stronger use case.

Change indicated: separate developer installation from operator use; provide
stack-specific examples, safe manifest generation, an actionable readiness list
and an honest list of owner responsibilities. Preserve keychain references,
approved destinations, writer consistency and owner review.

### F4. High: a checked connection is not necessarily writable for the selected policy

General `write_ready` checks permissions and application consistency capabilities.
The Stripe coordination condition is enforced later by `_ready` when a write
plan is requested. The reference advertises no external payment coordination.
The Studio plan button uses the general connection readiness value, not readiness
for the currently selected Stripe-authoritative policy.

Evidence: `agentbus/repair/live/service.py:130`,
`agentbus/repair/live/service.py:151`,
`studio/src/components/ConnectedRepair.tsx:70`,
`docs/product/production-customer-repair.md:89`.

Likely effect: a company can check credentials, choose Stripe, analyze/select
records and still discover at plan creation that this target cannot perform
the selected correction. No real Stripe account was exercised in this review;
this finding is derived from the current readiness and enforcement paths.

Change indicated: expose policy-specific readiness and the exact missing guarantee
before analysis/approval. Do not enable unsafe external-payment writes or replace
Stripe authority with application authority merely to remove the block.

### F5. High: refresh and reconciliation have importantly different side effects

`refresh_ids` stops previous non-completed/non-stopped plans before attaching a
new scan. Studio labels the action Refresh authoritative evidence without
showing that consequence at the action. A normal page reload is not this action;
the issue is the explicit new-scan button.

Evidence: `agentbus/repair/live/service.py:184`,
`studio/src/components/ConnectedRepair.tsx:68`.

Likely effect: an operator intending to inspect status can invalidate a paused or
approved plan, then be surprised that it cannot simply continue. Reconcile saved
operations is the non-mutating status path, but that distinction requires expertise.

Change indicated: separate Read current results from Replace evidence/review, and
explain the target-fencing consequence before creating a new scan. Keep fencing
and approval invalidation; make their consequences predictable.

### F6. High: in-flight execution lacks an operator-friendly pause/progress surface

Studio waits for execute before saving its returned result. The shared busy flag
disables Pause during that request. Emergency stop intentionally remains available.
The reversible pause API exists, but its UI action is unavailable at the moment
an operator may want to pause a running batch. The captured completed plan also
still presents Pause new dispatch and Emergency stop actions.

Evidence: `studio/src/components/ConnectedRepair.tsx:74`,
`studio/src/components/ConnectedRepair.tsx:76`; prior captured completed-plan screen
in `.tmp/production-repair-final-head-acceptance/verified-after-lost-response.png`.

Likely effect: the operator sees a busy request, not incremental per-record progress,
and may have to use permanent stop instead of pause. Stale lifecycle controls make
the completed state harder to interpret. This was inspected in source/screens,
not reproduced as a fresh live-browser timing test in this session.

Change indicated: persist/poll or stream bounded progress, allow pause through an
independent request, retain emergency stop, and derive enabled actions from the
actual lifecycle. Never imply cancellation reverses an external commit.

### F7. High: technical outcomes need a customer-actionable incident summary

Connected result rows lead with a truncated operation ID. The plan lists purchase
IDs, but result rows do not directly show that mapping. Exports contain JSON
plan/outcome data from which an engineer can reconstruct it. Status/counts describe
the selected plan; a completed one-record canary is not an incident-wide resolution.

Evidence: `studio/src/components/ConnectedRepair.tsx:71`,
`studio/src/components/ConnectedRepair.tsx:75`,
`studio/src/components/ConnectedRepair.tsx:85`,
`docs/product/customer-repair.md:36`.

Likely effect: support asks which purchase still needs action and why. A manager
can mistake completed/succeeded for everyone being fixed, despite technically
accurate batch counts. Masking account IDs is appropriate, but should not make
approved pseudonymous purchase/result matching difficult.

Change indicated: show purchase ID, verified result, reason and next action on
each row. Separate batch completion, remaining known candidates and unknown cohort
coverage. Offer a bounded support-ready export with safe identifiers, not raw PII.

### F8. Medium: large-list ergonomics trail the supported read volume

Search filters the currently loaded decision page, explicitly labelled Search
decision page. Decision pages hold 50 rows; the UI requires individual record
selection, with a maximum approved write cap of 200 and a default cap of one.
This is honest and bounded, but a support operator with thousands of IDs has a
different job from a developer demonstrating one canary.

Evidence: `studio/src/components/ConnectedRepair.tsx:63`,
`studio/src/components/ConnectedRepair.tsx:69`,
`studio/src/components/ConnectedRepair.tsx:70`.

Change indicated: server-scoped search, durable filters and explicit bounded batch
selection with an immutable reviewed selection. Do not silently turn Select all
into an expanding scope or remove the one-record default.

### F9. Medium: restoring access is narrower than making a customer whole

The supported operation restores the original purchased period. Expired periods
require investigation. Refunds, compensation, lost time, ticket replacement,
physical fulfillment and arbitrary data corrections are not this operation.
Customer communication, assignment and cross-team incident closure are not exposed
as built-in steps in the reviewed repair surfaces; an export is the current handoff.

Evidence: `agentbus/repair/live/contracts.py:78`,
`agentbus/repair/live/decisions.py:33`,
`docs/product/production-customer-repair.md:3`,
`studio/src/components/ConnectedRepair.tsx:75`.

Likely effect: even a correctly restored account may have lost a week of service.
The current success does not compensate that loss or establish that the customer
was notified. This does not justify automatically granting a fresh period.

Change indicated: name the exact business outcome, keep unresolved obligations
visible and offer an explicit handoff. Treat any compensation as a separate future
domain contract and separately approved operation.

## Twenty simulated users

All reactions and net-value judgments below are predictions, not participant
quotes. Suitable means suitable after the required company integration and
validation, not independently proven at a real company.

| # | Persona / domain | Problem after the bug fix | Likely setup/workflow/output experience | Net value for this task |
| --- | --- | --- | --- | --- |
| 1 | Founder, B2B SaaS | 300 paying customers remain locked out. | Understands the promise; expects to connect Stripe and begin. Meets developer integration, missing cohort discovery and possible external-payment coordination block. | Conditional. Useful once prepared; a first incident is not self-service and may be delayed. |
| 2 | Backend engineer, subscription learning platform | Valid monthly course memberships lack application access. | Can map purchases and writer hooks. Exact canary, later-change protection and independent access checks provide credible evidence. | Strong fit for an integrated application-owned workflow. Does not recover lost learning time or expired benefits. |
| 3 | Support lead, digital publisher | 3000 paid readers cannot open subscriber articles. | Has ticket IDs/emails or CSV, not a reviewed JSON snapshot or commit references. Page-only search and manual batch selection add work. | Workflow fits, but current setup/intake is not independently usable by this operator. |
| 4 | On-call SRE, streaming platform | Access rows were fixed, but a stale authorization projection still denies playback. | Values committed-versus-verified separation. Needs per-purchase progress, usable in-flight pause and a clear unresolved queue during an incident. | Useful after the app integrates its real normal-access/projection checks; not proof of every customer's device playback. |
| 5 | Community manager, paid forum | Forty valid paid memberships still have restricted forum access. | Own application authorization could fit, but the manager cannot implement the backend contract. External Discord/community-provider permissions are not automatically covered. | Conditional fit for the company's forum; needs an engineering handoff. |
| 6 | Part-time IT administrator, nonprofit membership portal | Twelve annual digital memberships remain locked. | The purchased-access rule may fit. Writer-hook integration and long-term maintenance can be disproportionate for twelve one-off cases. | Can solve the narrow issue after integration; setup may cost more effort than the existing support process. |
| 7 | Fulfillment manager, e-commerce | Paid orders were never sent to the warehouse. | Supplied contract repairs feature access, not warehouse orders, inventory, shipment creation or delivery proof. | Does not solve this workflow. Attempting setup as a general repair tool adds delay. |
| 8 | Finance operator, marketplace | Sellers are missing payouts after a settlement bug. | Needs ledger reconciliation and authorized money movement. Access grants and the Stripe read connector do not issue payouts. | Does not solve it; displaying access repair as financial recovery would be misleading. |
| 9 | Payments engineer, fintech | A fixed retry bug already charged some customers twice. | Can use engineering tools to understand a code fix, but the customer-repair operation neither establishes duplicate-charge causality nor refunds money. | Does not perform the required correction. Needs a distinct payment-reconciliation/refund contract. |
| 10 | IT lead, healthcare portal | A mapping bug hid patient documents from the right accounts. | Patient identity, consent and document authorization differ from paid-purchase entitlement. Missing records cannot safely be treated as missing subscription access. | Unsupported task. Do not generalize this contract to sensitive identity/clinical records. |
| 11 | Operations analyst, insurance | Generated policy documents contain incorrect coverage terms. | Needs policy/version authority, document correction and a reviewed communication process. No such domain operation is provided. | Does not solve it; access restoration is the wrong business effect. |
| 12 | Booking operations lead, travel | Paid reservations lost their confirmed seats. | Needs inventory, supplier confirmation and possibly rebooking/refunds; a purchased-feature boolean is insufficient. | Unsupported. The generic promise could waste urgent recovery time. |
| 13 | Operations lead, event ticketing | Customers paid but their admission QR codes are invalid. | Needs ticket issuance, seat ownership and admission-system validation. Checking the reference application's access flag is not gate verification. | Unsupported without a separate ticketing contract and integration. |
| 14 | Live-operations specialist, gaming | Purchased virtual currency was not credited. | Needs exact balance arithmetic, spend history and credit-specific semantic uniqueness. The shipped effect only grants access for a period. | Unsupported credit repair; do not repurpose a feature-access grant. |
| 15 | Provisioning engineer, telecom | Paid data packs were never activated on the network. | Needs carrier provisioning, quotas and network-level verification. No carrier connector is supplied. | Does not repair this workflow as shipped. |
| 16 | Customer operations manager, logistics | A bug sent parcels to incorrect delivery addresses. | Needs physical interception/rerouting and delivery evidence, not software authorization. | Cannot repair the physical outcome; an attempted setup adds work without the needed action. |
| 17 | Payroll administrator, HR software | Some employees received incorrect net pay. | Requires payroll calculations, records and authorized financial corrections. The purchase-to-access policy cannot express that effect. | Unsupported, with consequences that make improvising an access adapter inappropriate. |
| 18 | Case officer, public-service portal | Submitted permit applications disappeared after a release. | Needs authoritative application records and process-state restoration, not proof of a paid feature period. | Unsupported record recovery; current evidence formats do not cover this job. |
| 19 | Incident lead, engineering agency | Multiple clients repeatedly have valid paid accounts left unprovisioned. | Can invest in repeatable adapters and evidence. Each client still needs isolated configuration, scope and writer review; local exports do not become a shared multi-approver incident workspace. | Potentially valuable repeated use, but integration and cross-client collaboration remain substantial work. |
| 20 | Data engineer, analytics platform | A migration corrupted historical customer classifications. | Dates and a fixed PR are not authority for arbitrary field restoration. The repair policy cannot express a general migration/backfill or infer original values. | Does not provide this production correction; generating a script with the coding runtime is not equivalent to a verified repair integration. |

## What already earns trust

- Synthetic demo and real target are not silently interchangeable.
- Imports/candidate IDs do not authorize writes.
- The system distinguishes a committed mutation from independently verified access.
- Later refunds/manual changes, unknown outcomes and duplicate requests have real
  safety mechanisms instead of an optimistic success animation.
- Credentials stay out of the browser and production writes require exact scope.

These are product strengths. The answer to onboarding friction is not to remove
them. The product must translate them into understandable readiness and actions.

## Changes indicated, in dependency order

1. Establish one supported job: valid paid access missing after a fixed incident.
   Say who operates Syndra, what the company must integrate, what it cannot repair
   and what the user supplies. Preserve a separate entry for general coding work.
2. Add connection- and policy-specific readiness before inviting a real incident.
   Distinguish developer setup, operator readiness, sandbox evidence and a separately
   authorized real deployment. Surface external-payment blocks early.
3. Make checked-connection plus incident details plus candidate CSV a direct path.
   Show the cohort coverage limit. Do not require hand-authored transport JSON to
   reach the first authoritative preview.
4. Make the incident workbench action-oriented: review one purchase, approve the
   exact effect, verify it, then explicitly review a larger bounded batch.
5. Fix lifecycle communication: separate reconciliation from evidence replacement,
   make active pause available, show incremental outcomes and explain stop honestly.
6. Make results support-ready: purchase/result mapping, remaining work, uncertainty,
   last observation and next responsible action. Keep batch completion distinct
   from complete customer resolution and unknown incident-wide coverage.
7. Add further domain operations only through separately tested business contracts,
   not a generic unrestricted production SQL/HTTP action.

Proposed usability targets, not measured results: a new operator can identify
whether their incident fits without a developer explanation; a prepared operator
can reach an accurate preview without authoring JSON; every unresolved purchase
has an understandable next action; reading progress never silently replaces an
approval; a completed batch cannot be mistaken for closing an entire incident.

## Evidence collected for this assessment

- Reviewed current README, first-run guide, repair landing/import workflow,
  Connections component, connected-plan UI, authority/domain contracts and service
  implementation. Product code was not edited for this assessment.
- Re-ran the actual CLI in a new owned Git workspace with deterministic/offline
  environment and isolated `.syndra` state. New connections list was empty. Demo
  preview contained 100 records: 65 repairable, 15 correct, 10 ineligible and 10
  unresolved. A one-record correction restored one record. A duplicate request
  and explicit result lookup returned the same saved batch. No real customer
  mutation or provider/model call was made.
- CLI evidence: `.tmp/persona-review-cli.json`; disposable workspace
  `C:/tmp/syndra-persona-review-owgzwnth`. Evidence was preserved, not cleaned away.
- Focused UI component regression command: `npm.cmd test --
  src/components/ConnectedRepair.test.tsx src/pages/RepairPage.test.tsx`, from
  `studio/`. Result: 12 passed in two files. Automated assertions passing does not
  establish that a new human understands the product.
- Inspected previously captured installed-flow desktop/mobile screenshots and
  browser evidence in `.tmp/production-repair-final-head-acceptance`. That earlier
  run reported five states and zero automated accessibility violations. This is
  prior local acceptance evidence, not 20 participants or a new live usability test.
- No browser surface was available to the current computer-use session; an attempt
  to open the in-app browser returned Browser is not available. No alternative UI
  automation was used to bypass that restriction.
- No live provider/staging verification, deployment, production access, paid call,
  commit or push was performed. Existing application changes and unrelated files
  were preserved. This review adds only this assessment document.

## Validation still needed with real people

Recruit users from the actual target segment, rather than treating these 20
scenarios as research participants. Include a backend integrator, an on-call
operator, a nontechnical support lead and the incident owner who consumes results.
Use a disposable target and observe unaided setup, importing the records they
normally have, a changed/refunded purchase, a lost response, pause/reconcile and
report handoff. Record task completion, wrong turns, requests for help and incorrect
interpretations of success. Define measurement criteria before testing; do not
invent time-to-value, satisfaction or conversion numbers from this simulation.
