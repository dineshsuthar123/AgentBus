# Known Limitations

- Syndra is a public beta and not an unattended production service.
- Managed tools are hardened and bounded but do not provide VM/container-level
  isolation or a formal proof of generated-code safety.
- Studio currently runs as a browser application; the optional Tauri desktop
  shell was deliberately deferred to protect P0/P1 quality.
- Installed Studio starts with `syndra studio --workspace .` and authenticates
  through a single-use, 30-second bootstrap exchange. No manual daemon-token
  entry is required. Source contributors must build and stage the Studio assets
  first; see [Studio launch and security](studio.md).
- The payment fixture uses Maven offline mode. Required plugins and JUnit
  artifacts must already exist in the human operator's local Maven cache.
- The deterministic payment profile demonstrates an approval pause and a valid
  repair. It does not intentionally force a failed first candidate on every run;
  retry visualization appears when the real runtime records a retry.
- Offline replay can reuse captured results or simulate mutations. It is not
  always exact process re-execution, and Studio reports the actual mode/counters.
- Runtime filesystem edits are not automatically rolled back after a failed
  run. Syndra reports them for inspection and never resets or cleans user work.
- No checkout API, payment signature, webhook, real credential, or payment
  network integration is included. The fixture exercises local application
  correctness only.
- No live Azure, Ollama, payment-network, or other paid call is required for
  the Payment Safety Demo.
- The local daemon admits at most 64 concurrent REST requests and 32 event
  streams, with a shared rate of 100 requests per second and burst of 256.
  Capacity responses use HTTP 429 or 503 and `Retry-After: 1`; reconnect with
  backoff rather than opening more streams. These are protective defaults,
  not a supported multi-user capacity rating.
- After process loss, automatic retry before a task's first managed tool call
  requires the exact persisted workspace baseline and a remaining attempt.
  Workspace drift or uncertain tool effects stop for inspection. Completed
  tasks and terminal approved effects are not automatically repeated.
- The November 2026 release audit remains blocked pending its dependency and
  exact-candidate CI gates. See `docs/release-readiness/release-decision.md` in
  the source checkout; release audit reports are distributed separately from
  the Python package.
