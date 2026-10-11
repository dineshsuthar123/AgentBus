# Syndra Studio

Studio uses **Kinetic Precision**: a porcelain working surface, graphite
permission attachment, signal-orange selection and an original sculpted S.
A bounded working queue sits beside the selected artifact. Task, Plan,
Changes, Checks and Evidence examine one persisted run. Dependencies and
Activity remain explicit expandable views. Exact permission requests belong
to their operation; closing a queue item does not cancel or delete its run.

The optional 3D inspection object represents five lenses, **not sequential
execution steps or successful checks**. Real tasks, parallel dependencies,
verification and final review remain separately reported server records.
The object is paused initially on run pages, loads its renderer only when
mounted, and releases GPU resources on navigation or hiding. Reduced motion,
offscreen views and hidden tabs stop ambient animation. Unsupported WebGL or
context loss falls back to the same SVG identity without losing any controls.
Light is the initial theme; Workspace / Appearance offers dark and system.

## Installed launch

Install a prepared wheel with `ide` dependencies, then:

```console
syndra studio --workspace .
syndra studio --workspace . --no-open
```

The foreground CLI owns its daemon. Ctrl+C stops that process; closing a
browser does not. `--no-open` prints a short-lived numeric-loopback bootstrap
address; visit it within 30 seconds. The address contains no credential or run
payload. No Node/Vite process is needed for an installed wheel.

The launcher checks registry identity, process liveness, protocol, version and
state database before considering attachment. A compatible existing daemon
still requires authenticated access: the CLI has no access to VS Code
SecretStorage and **currently fails closed rather than attaching blindly**.
Use **Syndra: Open Studio** or **Open Current Run in Studio** from its connected
extension, keep an existing authenticated browser, or explicitly stop your
owned daemon before relaunching. Port collisions and incompatible versions are
errors, not an invitation to reuse an arbitrary server.

## Browser security

A transient loopback bootstrap serves an auto-submitting form with a 30-second,
single-use code in the POST body. The daemon binds the code to its exact origin,
bootstrap origin and optional run destination. Codes and browser sessions are
kept only as hashes in process memory. No durable bearer appears in URLs,
history, static assets, browser storage or logs.

Redeeming the code issues an HTTP-only, SameSite=Strict cookie. API requests
must reach the exact daemon host; session requests must be same-origin, and
mutations require the exact Origin. Logout revokes the session. Restart or
credential rotation requires a new launch; expired sessions do not imply a run
failed. Bearer authentication remains available to native clients and explicit
developer recovery. This is a local HTTP threat boundary, not a hostile-host
sandbox or remote multi-tenant service.

Only numeric-loopback HTTP is supported. Do not expose the daemon with a
public bind, reverse proxy, tunnel or permissive CORS. Remote VS Code browser
launch is intentionally unsupported without a separately designed secure
transport.

## First offline run

Choose **New task**, then **Create calculator guided workspace**. Syndra creates
a fresh marker-owned Git repository inside daemon-managed storage, with a
deliberately wrong addition function and a bundled standard-library unittest.
Launch the fixed objective. A deterministic provider requests a constrained
patch; real managed execution runs the unittest, verification and final review.
The test remains unchanged. Files and evidence remain available afterward;
failure does not automatically roll back edits.

**Guided demo is a fixed fixture, not arbitrary task intelligence.** Original
tasks require a checked live provider. The payment fixture additionally needs
the documented Java/Maven prerequisites and exact approval for its test
invocation; creating that fixture alone does not install those prerequisites.

## Workflow limits

| Request | Supported | Completion contract |
| --- | --- | --- |
| Multi, durable, sequential | Yes | Verification and mandatory final review |
| Multi, durable, bounded parallel | Yes | Isolated worktrees, integration, verification, final review |
| Multi, non-durable, sequential | API/CLI | Verification and final review; not resumable |
| Single, non-durable, sequential | Yes | Bounded AgentLoop; no final verifier/reviewer or commit/PR |
| Single, durable or parallel | No | Rejected before misleading accepted identity |

Studio exposes durable multi and non-durable single, not every API combination.
The guided repair always uses sequential durable multi. Genuine durable single
is still a scope limitation; state-store identity checks have not been weakened
or silently converted to multi. Reviewer rejection still prevents commit/PR.

## Provider setup

See [provider configuration](../guides/providers.md) and
[Azure authentication](../providers/azure-openai.md). Put secrets in the
launching process environment or the supported local credential facility,
never a generic browser JSON form. Restart deliberately after configuration
changes. Configuration validation is not reachability or model readiness.
Workspace / Connections shows not-checked, checked and failed states with a
check time. **Check live** is explicit, bounded and may incur Azure charges;
loading Studio performs no generation request. No automatic demo fallback is
enabled for submitted browser runs.

## Evidence and recovery

Changes uses the run's canonical repository and bounded diff. Checks separates
mechanical verification from final review. Evidence exposes trace integrity,
provenance and offline replay; captured work is not newly dispatched work.
Pending approvals render from their authoritative request records even when a
partial snapshot cannot construct the related graph node. The short command is
explicitly a bounded/redacted summary; expanded details disclose every scope
field and requested budget available in the API. Decisions bind to the actual
request and revision, and a conflict refreshes the record without resuming.
Replay remains pending/stale until the backend reports a terminal outcome.
Interrupted runs retain immutable attempts; permitted resume does not rerun
terminal successful tasks. The ledger uses server search and cursor pages,
not just a client-filtered recent hundred.

Run section/file and drawer links use nonsensitive hash parameters. The queue
holds at most 24 open runs and uses a readable selector on mobile; closing an
object does not cancel or delete persisted work. Sections, file selections and reading positions remain
in session memory. Loaded ledger pages and filters survive run inspection;
new events mark a saved history snapshot as potentially stale until refreshed.
Task drafts remain in memory while another run is inspected. Browser storage
holds only appearance and the last run ID/known section, never task prose, local
paths, approval payloads or credentials. The daemon validates that last selection
before root navigation restores it. Explicit deep links take precedence.
Stream authentication failure disconnects visibly. Bounded
retries preserve the last authoritative snapshot rather than inventing success.

## Contributor setup

```powershell
cd studio
npm.cmd ci
npm.cmd run typecheck
npm.cmd run lint
npm.cmd test
npm.cmd run build
cd ..
.venv\Scripts\python.exe scripts/prepare_studio.py
.venv\Scripts\syndra.exe studio --workspace .
```

For Vite development only, launch a known daemon with `syndra serve --port 8765
--json-ready`, run `npm run dev` in `studio/`, and use the explicit developer
recovery bearer form. The proxy stays numeric-loopback/same-origin. Never
persist the handshake, token or raw provider response in screenshots or logs.

Prepared assets have a sorted SHA-256 manifest and bundled Bricolage and Plex licenses.
Distribution audits require exactly the declared assets, in both archives, and
verify every hash. Reusing setuptools' `build/lib` with a wheel-only incremental
build can retain obsolete frontend chunks. For release/installed acceptance,
build the wheel from a fresh sdist with the standard paired build, not `--wheel`:

```powershell
.venv\Scripts\python.exe -m build --no-isolation --outdir .tmp/studio-dist
.venv\Scripts\python.exe scripts/verify_distribution.py .tmp/studio-dist
```

Build twice with the same `SOURCE_DATE_EPOCH` and use the existing
release-packaging comparison gate before asserting reproducibility. Never
delete user work or bypass an asset audit to make a contaminated archive pass.

See [The Living Workshop](living-workshop.md) for the current narrative and
interaction implementation, [Kinetic Precision implementation](kinetic-precision.md)
for its underlying visual architecture, and [earlier functional checkpoint](folio-implementation.md)
for the preserved runtime and navigation contracts. Historic screenshot
galleries are not acceptance evidence for this visual revision.
