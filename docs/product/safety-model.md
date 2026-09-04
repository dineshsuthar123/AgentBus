# Safety Model

Syndra applies multiple independent controls:

- canonical workspace and exact Git-top-level validation;
- versioned tools with independently derived capability scopes;
- deterministic policy before dispatch;
- exact invocation- and revision-bound approval, with no "always allow" path;
- `shell=False`, executable identity, bounded output, timeout, and cancellation;
- durable task/attempt state and immutable successful terminals;
- verifier and mandatory final-review gates before commit or pull request;
- bounded redacted logs, reports, errors, attempts, traces, and support data;
- content-addressed trace objects and provenance integrity verification;
- offline replay with explicit captured/simulated behavior and call counters.

Studio does not receive provider API keys, hidden environment variables, raw
prompts, unrestricted tool arguments, raw stdout/stderr, or Azure response
bodies. The browser never calls Azure directly.

These controls are not complete OS sandbox isolation and do not make generated
code intrinsically safe. Evaluate Syndra with least-privilege credentials and
disposable repositories. Filesystem edits are intentionally not rolled back
automatically after failure; the failed report and source view retain them for
human inspection.
