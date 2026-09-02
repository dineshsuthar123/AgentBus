# Known Limitations

- AgentBus is a public beta and not an unattended production service.
- Managed tools are hardened and bounded but do not provide VM/container-level
  isolation or a formal proof of generated-code safety.
- Studio currently runs as a browser application; the optional Tauri desktop
  shell was deliberately deferred to protect P0/P1 quality.
- Studio requires the local daemon to be started separately and the one-time
  token to be pasted into the connection screen.
- The payment fixture uses Maven offline mode. Required plugins and JUnit
  artifacts must already exist in the human operator's local Maven cache.
- The deterministic payment profile demonstrates an approval pause and a valid
  repair. It does not intentionally force a failed first candidate on every run;
  retry visualization appears when the real runtime records a retry.
- Offline replay can reuse captured results or simulate mutations. It is not
  always exact process re-execution, and Studio reports the actual mode/counters.
- Runtime filesystem edits are not automatically rolled back after a failed
  run. AgentBus reports them for inspection and never resets or cleans user work.
- No Razorpay Checkout, order API, payment signature, webhook, or real payment
  credential integration is included. Razorpay is a problem-domain narrative,
  not a partnership claim.
- No live Azure, Ollama, Razorpay, or other paid/provider call is required for
  the submission demo.
