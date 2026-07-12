# ADR 0003: Generated Artifact Hygiene

## Status

Accepted

## Context

Verifier commands can create bytecode, test caches, coverage output, and build directories inside a target repository. Treating those outputs exactly like source changes pollutes semantic review and can cause a correct durable run to be rejected. Hiding every generated-looking path is also unsafe because repositories may intentionally track fixtures or generated code, and unknown untracked files must remain visible.

## Decision

AgentBus uses one repository-relative `GeneratedArtifactPolicy` and a structured Git change inventory.

- Paths are canonicalized and traversal is rejected.
- Raw run-attributed paths remain available for audit and failed-run reporting.
- Normal tracked and untracked files remain relevant.
- Known untracked or Git-ignored generated artifacts are excluded from semantic diffs.
- Generated-looking tracked files stay in semantic review and are reported explicitly.
- Commit eligibility excludes generated and ignored paths, including tracked generated artifacts.
- Git operations remain scoped to the validated target top-level and use `shell=False`.
- Auto-detected pytest runs disable Python bytecode and pytest cache creation through a copied inherited environment and safe command arguments.
- Explicit verifier commands are not silently rewritten.
- No artifact classification triggers automatic deletion.

## Consequences

Reviewers receive source-focused evidence plus a compact generated/ignored artifact note. Durable reports expose raw, relevant, generated, ignored, review-excluded, and commit-eligible paths even when a run fails. Generated files may remain on disk and require manual cleanup. Pattern matching is intentionally conservative and cannot prove that every build output is safe to ignore; unknown paths remain reviewable.
