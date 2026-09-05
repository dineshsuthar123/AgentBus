# Environment variables

Environment variables override config files and CLI values. Syndra does not
load `.env`; set variables in the process that launches the CLI or daemon.

Canonical `SYNDRA_*` names take precedence over legacy `AGENTBUS_*` aliases.
When both forms differ, Syndra emits a deterministic conflict warning.

## Runtime and storage

`SYNDRA_WORKSPACE`, `SYNDRA_RUNS_DIR`, `SYNDRA_STATE_DIR`,
`SYNDRA_STATE_DB`, `SYNDRA_WORKTREE_ROOT`, `SYNDRA_KEEP_WORKTREES`,
`SYNDRA_MAX_STEPS`, `SYNDRA_COMMAND_TIMEOUT`,
`SYNDRA_MAX_HISTORY_CHARS`, `SYNDRA_DURABLE_EXECUTION`,
`SYNDRA_PARALLEL_EXECUTION`, `SYNDRA_MAX_WORKERS`,
`SYNDRA_WORKER_LEASE_SECONDS`, `SYNDRA_WORKER_HEARTBEAT_SECONDS`, and
`SYNDRA_INTEGRATION_STRATEGY` control local execution.

## Safety, index, daemon, and logs

`SYNDRA_POLICY_MODE` must remain `enforce` during the public beta.
`SYNDRA_REPOSITORY_INTELLIGENCE`, `SYNDRA_SEMANTIC_RETRIEVAL`,
`SYNDRA_TRACE_RETENTION_DAYS`, `SYNDRA_DAEMON_AUTO_START`,
`SYNDRA_DAEMON_IDLE_TIMEOUT_SECONDS`, `SYNDRA_LOG_LEVEL`, and
`SYNDRA_LOG_RETENTION_FILES` control optional local services and retention.

## Provider routing

`SYNDRA_PROVIDER`, `SYNDRA_FALLBACK_PROVIDER`,
`SYNDRA_ENABLE_PROVIDER_FALLBACK`, `SYNDRA_MODEL`,
`SYNDRA_OLLAMA_URL`, `SYNDRA_PLANNER_MODEL`, `SYNDRA_CODER_MODEL`,
`SYNDRA_REVIEWER_MODEL`, `SYNDRA_SUMMARIZER_MODEL`,
`SYNDRA_MODEL_TIMEOUT_SECONDS`, `SYNDRA_MODEL_MAX_RETRIES`,
`SYNDRA_MODEL_RETRY_BASE_SECONDS`, and
`SYNDRA_MODEL_RETRY_MAX_SECONDS` configure normalized routing.

## Azure OpenAI

`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`,
`AZURE_OPENAI_AUTH_MODE`, `AZURE_OPENAI_API_MODE`,
`AZURE_OPENAI_DEFAULT_DEPLOYMENT`, `AZURE_OPENAI_PLANNER_DEPLOYMENT`,
`AZURE_OPENAI_CODER_DEPLOYMENT`, `AZURE_OPENAI_REVIEWER_DEPLOYMENT`,
`AZURE_OPENAI_SUMMARIZER_DEPLOYMENT`, `AZURE_OPENAI_TIMEOUT_SECONDS`, and
`AZURE_OPENAI_MAX_RETRIES` configure Azure.

The API key is the only credential in this list. Never place it in Git, config
files, command arguments, issue reports, support bundles, or screenshots.

## Deterministic development and evaluation

`SYNDRA_DETERMINISTIC_PROFILE` selects a built-in offline profile. Latency and
failure variables are intended for bounded tests, not normal user setup.
Evaluation-specific limits use `SYNDRA_EVAL_MAX_REQUESTS`,
`SYNDRA_EVAL_MAX_TOKENS`, `SYNDRA_EVAL_TIMEOUT_SECONDS`,
`SYNDRA_EVAL_RESULTS_DIR`, `SYNDRA_EVAL_FIXTURE_ROOT`, and
`SYNDRA_EVAL_PRESERVE_FIXTURES`.

Use `.env.example` only as a placeholder reference. Syndra intentionally does
not load it.
