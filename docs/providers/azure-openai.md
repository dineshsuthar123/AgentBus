# Azure OpenAI Provider

AgentBus supports Azure OpenAI through the Azure OpenAI v1 interface and the official `openai` Python package. The adapter is isolated under `agentbus/models/azure_openai.py`; agents and durable execution use provider-neutral routes and results.

## Prerequisites

You need:

- An existing Azure OpenAI resource.
- One or more deployed models that support the API mode and structured-output features your workflow uses.
- The resource endpoint.
- An API key for this checkpoint.
- The Azure deployment names. Deployment names are user-defined identifiers and may differ from public model family names.

AgentBus does not create resources, deployments, keys, quota, or billing configuration.

## Configuration

AgentBus does not load `.env` files automatically. Export values in the launching process or use your normal secret manager. Never commit a populated `.env` file.

```dotenv
AGENTBUS_PROVIDER=azure
AGENTBUS_FALLBACK_PROVIDER=ollama
AGENTBUS_ENABLE_PROVIDER_FALLBACK=false
AGENTBUS_MODEL_TIMEOUT_SECONDS=180
AGENTBUS_MODEL_MAX_RETRIES=2
AGENTBUS_MODEL_RETRY_BASE_SECONDS=0.5
AGENTBUS_MODEL_RETRY_MAX_SECONDS=8

AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
AZURE_OPENAI_API_KEY=YOUR-KEY-FROM-A-SECRET-STORE
AZURE_OPENAI_AUTH_MODE=api_key
AZURE_OPENAI_API_MODE=responses
AZURE_OPENAI_DEFAULT_DEPLOYMENT=YOUR-DEFAULT-DEPLOYMENT
AZURE_OPENAI_PLANNER_DEPLOYMENT=YOUR-PLANNER-DEPLOYMENT
AZURE_OPENAI_CODER_DEPLOYMENT=YOUR-CODER-DEPLOYMENT
AZURE_OPENAI_REVIEWER_DEPLOYMENT=YOUR-REVIEWER-DEPLOYMENT
AZURE_OPENAI_SUMMARIZER_DEPLOYMENT=YOUR-SUMMARIZER-DEPLOYMENT
AZURE_OPENAI_TIMEOUT_SECONDS=180
AZURE_OPENAI_MAX_RETRIES=2
```

Empty strings are treated as unset. Azure variables are not required while Ollama is selected.

## Endpoint And API Mode

Supply either the resource origin or the v1 base path:

```text
https://YOUR-RESOURCE.openai.azure.com
https://YOUR-RESOURCE.openai.azure.com/openai/v1/
```

Both normalize to the second form. AgentBus rejects HTTP endpoints, embedded credentials, query strings, fragments, deployment-specific paths, and duplicated v1 paths. The v1 interface does not use a dated `api-version` in AgentBus configuration.

`responses` is the default and recommended mode. Set `AZURE_OPENAI_API_MODE=chat_completions` only when that is the intended deployment capability. AgentBus does not silently fall back between API modes. Requests are stateless (`store=false`) and Azure response IDs are observational metadata, never durable workflow state.

References:

- [Azure OpenAI v1 migration guidance](https://learn.microsoft.com/en-us/azure/developer/ai/how-to/azure-openai-to-responses)
- [Azure OpenAI API version lifecycle](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle)
- [Official OpenAI Python SDK](https://github.com/openai/openai-python)

## Authentication

This checkpoint implements:

```text
AZURE_OPENAI_AUTH_MODE=api_key
```

The endpoint, API key, and a deployment for the selected role are required. Entra ID, `DefaultAzureCredential`, and managed identity are intentionally not partially implemented. The provider boundary allows a future token-provider implementation without changing agents.

## Role Routing

Azure deployment resolution is:

1. CLI/active-provider role override, when supplied.
2. `AZURE_OPENAI_<ROLE>_DEPLOYMENT`.
3. `AZURE_OPENAI_DEFAULT_DEPLOYMENT`.
4. A local configuration error if neither exists.

Roles are `planner`, `coder`, `reviewer`, `summarizer`, and `default`. The deployment name is passed as the SDK `model` value. It must be the deployed Azure name, not merely a catalog model name.

## Structured Output

Planner, reviewer, and coder/action calls provide Pydantic schemas. For Pydantic schemas, the SDK parse helper requests schema-constrained output. AgentBus then validates parsed output locally and returns plain JSON-compatible data. For raw JSON Schemas, AgentBus sends strict schema format and validates locally with `jsonschema`.

AgentBus fails closed on:

- Missing output.
- Malformed JSON.
- Missing required fields.
- Invalid enum values or field types.
- Forbidden extra fields.
- Deployment rejection of configured structured output.

There is no silent downgrade to unconstrained text for structured agent actions. Ollama retains its bounded legacy JSON extraction for compatibility and still performs local Pydantic validation.

## Retry And Fallback

Provider-call retries happen inside one durable task attempt. Retryable categories are timeout, transport/connection, transient 429, and temporary 5xx/service failures. `Retry-After` is respected up to the configured maximum; otherwise AgentBus uses capped exponential backoff with jitter. The OpenAI SDK's own retry loop is disabled so AgentBus owns one observable retry budget.

The following are not provider-retried: authentication, authorization, quota exhaustion, invalid configuration, deployment not found, bad request, unsupported structured output, content policy, malformed output, and local schema failure.

Fallback is off by default. When both `AGENTBUS_ENABLE_PROVIDER_FALLBACK=true` and `AGENTBUS_FALLBACK_PROVIDER=ollama` are set, exhausted retryable Azure failures may use Ollama. No other automatic provider direction is supported.

Fallback never applies to auth/configuration/policy/output/safety/approval failures. The result and events retain original provider, safe error category, final provider, retry count, and fallback indication. Durable task retry limits remain a second finite outer budget.

## Diagnostics And Live Test

These commands do not contact Azure:

```bash
python -m agentbus.main --list-providers
python -m agentbus.main --show-model-config
python -m agentbus.main --check-provider azure
```

The check prints endpoint host, auth mode, API mode, selected deployment, readiness, and actionable missing-variable errors. It never prints the key.

After setting real credentials, make the first explicit connectivity request with:

```bash
python -m agentbus.main --check-provider azure --live
```

`--live` sends one tiny deterministic structured request, does not start a durable run, and does not modify the repository. It can incur a small Azure charge.

## Troubleshooting

### Missing deployment

Set the role-specific deployment or `AZURE_OPENAI_DEFAULT_DEPLOYMENT`. Confirm that the value is the Azure deployment name.

### 401 Authentication

Check `AZURE_OPENAI_API_KEY` and confirm that it belongs to the endpoint's resource. Rotate any key that may have been exposed. Keys are not validated during import.

### 403 Authorization

Confirm resource access and Azure policy. This is not retried or sent to Ollama fallback.

### 404 Deployment Not Found

Confirm endpoint/resource pairing and exact deployment spelling. Public model names are not substitutes for deployment names.

### 429 Rate Limit

AgentBus honors bounded retry guidance and then fails or uses explicitly enabled Ollama fallback. Reduce concurrency or request appropriate quota outside AgentBus.

### Quota Exhaustion

Quota exhaustion is classified separately from a transient rate limit and is not automatically retried/fallen back. Review Azure quota and billing outside AgentBus.

### Timeout Or Connection Failure

Check endpoint reachability and `AZURE_OPENAI_TIMEOUT_SECONDS`. Transient errors use bounded retries and may use explicit Ollama fallback after exhaustion.

### Structured Output Unsupported

Not every deployment supports strict structured output or every API mode. Choose a compatible deployment or explicitly select a supported mode. AgentBus does not silently degrade structured actions to free text.

## Secret Handling

- Never place keys in endpoint query strings, prompts, task text, repository files, or CLI arguments.
- Use environment injection or a secret manager; do not commit populated `.env` or credential files.
- Config repr and diagnostics expose only whether a key is configured.
- Provider events exclude prompts/responses and include only safe routing, status, timing, request ID, and usage fields.
- Shared redaction covers secret-shaped keys, assignments, bearer tokens, and URL query strings for JSONL and SQLite boundaries.
- Exception messages are adapter-authored and do not echo raw SDK bodies, headers, request payloads, or keys.
