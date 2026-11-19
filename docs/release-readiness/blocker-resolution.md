# Release blocker follow-up

The November 2026 readiness reports describe the dirty source tree identified
by their recorded digest. The follow-up commit carries the changes below.
The historical counts, artifacts, and outcomes remain tied to their recorded
revisions. Follow-up command logs and metadata use the `blocker-` prefix under
`.tmp/release-readiness/20261118/`. Each command record includes its Git HEAD,
runtime, environment, timestamps, exit status, and log hash.

## Dependency review

The original audit's unique advisories are GHSA-fc5q-39xc-vjpf (picomatch),
GHSA-3g3m-2fjr-6pw4 (glob), and GHSA-g3hv-75wg-m249 (minimatch).
The VSIX packager uses scoped overrides for glob 14.0.2 and minimatch 11.0.9.
The affected functions remain covered by package build and content checks.

Studio resolves picomatch 4.0.7 through Vite, Vitest and tinyglobby. The extension
toolchain resolves picomatch 4.0.7 through typescript-eslint and tinyglobby,
and 2.3.2 through the packager's secretlint dependency. Production-only
dependency trees exclude these packages. The installed Studio assets are browser
bundles. The VSIX contains the compiled extension and its manifest and resources,
with development dependencies excluded.

The install paths fetch pinned registry dependencies. No Syndra UI or daemon
input reaches the glob compiler during that lifecycle. Compiler and test
configurations and staged checkout files have a different trust boundary.
Contributors can modify build-time patterns, Vite module import patterns,
stylesheet and source inputs, and the VSIX packager's ignore file. A build of
untrusted checkout content can therefore encounter untrusted patterns. This is
toolchain availability exposure. Its scope includes contributor builds and
disposable PR jobs even though the shipped runtime dependency trees are clear.

The registry's latest picomatch is 4.0.7. The reviewed advisory lists no patched
release. Upstream PRs 237 and 270 were open at review. A newly published third-party
fork tracks one of those proposals and has limited review history. A small local
backport would also need compatibility evidence for two different major versions
and could leave or obscure scanner findings. The high audit remains a failure.
[Advisory](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) ·
[Upstream PR 237](https://github.com/micromatch/picomatch/pull/237) ·
[Upstream PR 270](https://github.com/micromatch/picomatch/pull/270)

**Proposed exception for founder review.** If the founders need an internal
preview before upstream remediation, restrict it to reviewed local builds from
trusted commits and disposable, time-bounded read-only PR runners. Keep build
and signing credentials out of PR jobs. Record the owner, affected lockfile
entries, approved commits, and a review deadline of December 2, 2026. Retract
the exception at the deadline or when an upstream release can be qualified.
The audit remains enabled and failing. This proposal does not qualify external
distribution or satisfy the clean security gate.

## Windows write failure

The historical cycle 7 stopped at invocation `soak-write-00007` in run
`soak-2026-00007`. It attempted to write the generated worker's existing Python
file before any lease, cancellation, replay, or worktree operation for this
cycle. The old report omitted the worker slot and native exception details.
Index updates and writes can overlap because the index lock serializes updates
only. Each worker has its own target file.

A controlled native test reproduces the write failure using the same binary
read mode as the index inventory. Windows reports access denied if the reader
holds the target open during atomic replacement. The fix retries only Windows
replacement errors 5 and 32, with at most 70 ms total scheduled delay. It
revalidates the destination, staged file and root identity before trying again.
Persistent lock and target-drift tests check that the old file survives and the
temporary file is removed. The native race is verified. The historical cycle's
cause remains an inference unless its missing error details can be recovered.

The diagnostic soak harness retains an owned synthetic fixture along with
invocation attribution, error codes, thread and handle measurements and observed
replacement state. Its evidence supplements the original one-hour failure and
the standard soak gate. A clean extended run is required for release.

Candidate validation also found a Windows liveness false positive in the
release acceptance oracle. A child returned exit code zero while its Popen
object retained the handle. The old probe still reported it as alive.
Registry and release acceptance now use the process object's exit signal.
The debugger-independent regression holds the handle across the exit.
Control acceptance keeps its local loopback session active while testing
cooperative replay from the client. The final idle-shutdown check stays intact.

## WebKit and validation environment

Packaged Studio reproduced mixed light and dark colors when the Connections
drawer switched themes. Several text elements retained the light foreground
while their containers used the dark palette. Queue IDs, trace labels, loading
messages, partial-state messages and the appearance label now resolve their
foreground directly from the active theme tokens. The browser journey waits for
the actual Connections content and verifies the drawer background before it
captures and scans the dark state. Axe and browser error checks remain enabled.

The full Python rerun uses a clean checkout of the candidate and a short
temporary fixture root. An earlier Windows run hit Git's metadata path limit
while creating disposable worktrees. The same checkout also timed out scanning
its ignored evidence for the release report. Both failed runs and the Windows
fixture state databases remain available in the local evidence directory.

## Indexing

The 10,000-file profiler counts file reads, parser reuse, graph invalidation,
reference resolution, snapshot database operations, and publication. A single
edit reparses one file and reuses 9,999 parser records. It still enumerates the
checkout, reads and hashes all source files, and rebuilds and publishes the
versioned graph. The before profile is `blocker-index-before.log` and the
follow-up profile is `blocker-index-after.log`. These workloads invoke a direct
update and have no watcher activity.

The path resolver now checks existence and native link attributes with one
no-follow stat per component. It retains canonical containment and root identity
checks. Inaccessible components fail closed. The change leaves content hashing,
dependency invalidation, and cancellation in place.

## Required CI and review

The 40 branch protection contexts match the workflow job and matrix names.
Release Security consumes the wheel, sdist and VSIX built and audited by its
prerequisite jobs. Dependency Security executes both npm audits and preserves
their failure status.

The repository currently lists one collaborator with review permissions,
the authenticated owner. The author's own approval does not satisfy the required
review. The last-push review rule also requires a different reviewer. An eligible
second maintainer must be arranged before the current review rules can be met.
This follow-up retains the existing protection settings.

Final packaging, test and soak logs, the candidate commit, and the release verdict
are recorded in `blocker-results.json` in the local evidence directory. The release
decision stays blocked until both the required local gates and exact-commit CI pass.
