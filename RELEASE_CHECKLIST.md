# AgentBus release checklist

Use this checklist from a clean release branch. Record evidence; do not infer
success from an earlier run.

## Preparation

- [ ] Confirm the intended version in `agentbus/_version.py` and changelog.
- [ ] Review `git status`, branch, commit history, and the complete diff.
- [ ] Confirm dependencies, license metadata, entry points, and package data.
- [ ] Review tracked and staged files for credentials and runtime artifacts.

## Offline verification

- [ ] Run `python -m compileall agentbus`.
- [ ] Run the complete pytest suite.
- [ ] Run `agentbus-eval run --suite release-offline --variant durable-parallel-fake` twice.
- [ ] Compare semantic results; ignore run IDs, timestamps, and durations.
- [ ] Run `python -m build` and inspect wheel and sdist contents.
- [ ] Install the wheel in a fresh virtual environment with no repository-root `PYTHONPATH`.
- [ ] Verify `agentbus --version`, `agentbus --help`, offline `agentbus doctor`, and `agentbus-eval list`.
- [ ] Run `git diff --check`.

## Optional live verification

- [ ] Confirm secrets are supplied only by the CI/release environment.
- [ ] Manually run `release-azure-smoke` with `--live --repeat 2` and strict budgets.
- [ ] Confirm fallback, push, PR creation, and source-checkout mutation remain disabled.

## Evidence and publication

- [ ] Generate Markdown and JSON release reports; leave missing checks as `NOT_RUN`.
- [ ] Review known limitations and security guidance.
- [ ] Commit the release branch and ensure the post-commit worktree is clean.
- [ ] Obtain maintainer approval before tagging, publishing, or creating a GitHub release.
- [ ] Never publish from an unreviewed or dirty worktree.
