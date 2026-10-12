# Hosted Execution: Security And Free-Tier Plan

Status: proposed, not deployed or implemented. The public showcase and installed
local Studio remain separate products. This document does not enable remote
execution, create cloud resources, or authorize a paid plan.

## Account Decisions Needed

The first hosted release needs an explicit repository scope (public invite-only
beta or private per-user environments), a gateway account, and a compute account.
Cloudflare and Oracle deployment access has not been verified. The existing
GitHub CLI session cannot manage Codespaces without additional account consent.
Do not reuse that session's broad credential as a hosted service credential.

The deployment must wait for account-owner sign-in, application registration and
free-plan verification. Never request credentials in chat, put them in source or
client assets, enable billing, or substitute paid compute when capacity is absent.

## Viable Hosting Choices

Pricing and limits were checked against primary documentation on 2026-10-12.
Recheck them immediately before provisioning; these are not perpetual guarantees.

| Component | Candidate | Constraint |
| --- | --- | --- |
| Existing public preview | GitHub Pages | Static showcase only; no backend or visitor workspace access |
| Hosted UI and gateway | Cloudflare Workers Free | Same-origin assets and API; 100,000 dynamic requests/day and 10 ms CPU/invocation |
| Gateway metadata | Cloudflare D1 Free | 5 million rows read/day, 100,000 rows written/day, 5 GB total storage |
| Shared beta compute | Oracle Always Free VM | Only verified eligible resources in the tenancy's home region; capacity shortages and idle reclamation are possible |
| Private per-user compute | GitHub Codespaces | Personal Free includes 120 core-hours and 15 GB-month; a 2-core machine consumes two included hours per elapsed hour |
| Optional free inference | Cloudflare Workers AI Free | 10,000 neurons/day shared by the account; only models explicitly available without paid billing |

Cloudflare Workers cannot directly host the existing native Python/Git process
runtime. Cloudflare Containers require Workers Paid and are excluded. GitHub
Actions is for a repository's software lifecycle, not a generic public SaaS
worker pool; do not dispatch visitor repositories through this project's CI.

An Oracle VM is a candidate for bounded shared execution, not an available or
provisioned resource. Provider eligibility, remaining quota, region, image,
storage, networking, and account state must all be verified before creation.
Do not rely on expiring trial credits or old advertised A1 resource sizes.

Codespaces is user-owned hosted development, not an anonymous shared execution
service. GitHub authenticates the owner and isolates each codespace's network.
Keep ports private. The existing Studio deliberately does not accept forwarded
public origins; Codespaces does not waive the need for a new secure browser
transport. Native workspace extension/CLI support is a separate integration path.

## Non-Negotiable Boundaries

1. Keep the local daemon numeric-loopback-only with its current authentication,
   exact-origin checks and approval semantics. Never add a public bind, tunnel,
   permissive CORS, trusted-forwarded-header bypass, or cloud-origin exception.
2. Create a separate HTTPS hosted gateway and browser entry point. Use registered
   GitHub sign-in, single-use OAuth state, secure HTTP-only cookies, session
   rotation/revocation, exact-origin mutation checks and CSRF protection. A valid
   login is not authorization to another user's repository or run.
3. Bind every workspace, run, attempt, event, approval and artifact to its owner.
   Recheck repository access server-side. Do not trust browser-supplied owners,
   workspace paths, credential scopes, completion claims or artifact URLs.
4. Run untrusted tools/tests in a fresh, independently isolated job sandbox,
   outside the trusted coordinator's security principal. The existing process
   supervisor is not an OS sandbox. Require an evaluated sandbox runtime such as
   gVisor or a microVM; ordinary shared-user subprocesses are not sufficient.
5. Give jobs no service credentials, host home, host root, control-plane state,
   Docker socket, privileged mode, host network/PID namespace or cloud metadata
   access. Use digest-pinned images, non-root jobs, denied capabilities, read-only
   roots, contained ephemeral workspaces and enforced CPU/memory/PID/disk limits.
6. Default untrusted job egress to denied. Trusted fetch/model brokers validate
   destinations and enforce byte/token limits. Repository metadata must not
   select arbitrary URLs, local IPs, redirects, installs, images or commands.
7. Issue short-lived worker credentials scoped to one owner, run and immutable
   attempt. Authenticate callbacks and artifact uploads, prevent replay, bound
   streams and archives, and reject traversal, links and duplicate artifact paths.
8. Preserve mandatory verification and final review before completion or any
   commit/PR action. Exact approvals bind to immutable inputs/revisions. Disable
   automatic repository writes in the initial beta; export a reviewed patch.
9. Keep immutable successful attempt history on failure/resume. Retry only
   eligible nonterminal work. Failure retains artifact reporting and never
   automatically resets, cleans or rolls back repository changes.
10. Store redacted, bounded metadata in owner-scoped managed storage. Do not
    persist prompts, credentials, raw provider bodies or unrestricted tool
    arguments in logs. Source-derived artifacts are private, even for a public
    repository. No public CI artifacts or logs may carry a visitor's payload.

## Proposed Initial Application Limits

These are product limits to implement and test, not current enforcement or
provider entitlements. Private multi-tenant execution stays disabled until its
additional storage, authorization and sandbox gates pass.

- Invite-only accounts; repository scope chosen explicitly before launch.
- One active run per account; at most two active runs for the shared beta.
- Three admitted runs per account per UTC day; twenty total per day.
- Ten-minute wall-clock ceiling per run, counting retries and verification.
- Job ceiling: one CPU, 1 GiB RAM, 64 PIDs, 256 MiB writable storage.
- Repository ceiling: 10 MiB selected source and 2,000 files; reject larger input.
- Event output ceiling: 1 MiB per run; exported patch ceiling: 5 MiB.
- Artifact retention: 24 hours, with visible export and expiration warnings.
- Reserve limits transactionally before dispatch. Charge retries to the same
  reservation; idempotency, concurrent requests, crashes and resume cannot bypass
  admission. Exhausted quotas return an explicit refusal, not a paid fallback.
- Start with deterministic guided fixtures for end-to-end validation. Arbitrary
  tasks require a separately verified, consented free model; never describe a
  fake fixture as real model reasoning. Do not call live Azure during deployment.

Provider-enforced Free plan stops and application quotas are both required.
Alerts alone are not spending caps. Disable the hosted executor if free-plan
eligibility, sandbox health, credential isolation or quota storage cannot be
verified. No automatic upgrade, autoscaling into paid shapes or provider fallback.

## Deployment Gates

Before enabling a hosted Run button:

- Verify account ownership, provider Free plan, remaining resource quota and a
  hard no-paid-fallback configuration. Provision only the reviewed resource set.
- Test sign-in, logout/revocation, CSRF, exact origin, owner authorization, run
  enumeration, approval revision binding and worker callback replay rejection.
- Test hostile repositories: path/link escapes, archive bombs, command injection,
  credential probes, metadata/egress probes, fork bombs, runaway output and
  timeout/cancellation. Prove termination of descendants and isolation from the
  next job; reject a host without the required sandbox runtime.
- Test parallel quota reservations, idempotency, retry charging, crashed leases
  and quota exhaustion. An unavailable database must refuse new execution.
- Run actual isolated success/failure/resume/parallel acceptance with persisted
  run IDs, correct managed log paths, mandatory review, and failed-run artifacts.
- Run the existing full Python, frontend, extension, security and packaging gates.
  Audit deployed assets, archives, logs and secret storage before publication.
- Verify backup/export and expiration behavior, resource reclamation handling,
  administrative disablement and rollback of service code without deleting user
  changes. Display free-tier availability limits; do not promise a production SLA.

## Primary References

- [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)
- [D1 pricing and free limits](https://developers.cloudflare.com/d1/platform/pricing/)
- [Cloudflare Containers pricing](https://developers.cloudflare.com/containers/platform/pricing/)
- [Workers AI pricing and paid-only models](https://developers.cloudflare.com/workers-ai/platform/pricing/)
- [Oracle Always Free eligibility and reclamation](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Codespaces billing and quota enforcement](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces)
- [Codespaces authentication and network isolation](https://docs.github.com/en/codespaces/reference/security-in-github-codespaces)
- [GitHub Actions product terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features)
- [GitHub Actions security guidance](https://docs.github.com/en/actions/reference/security/secure-use)
