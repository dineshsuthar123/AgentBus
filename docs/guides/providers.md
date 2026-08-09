# Providers

AgentBus has one provider contract and three adapters. Provider selection does
not bypass managed tools, verifier checks, final review, approvals, or durable
state.

## Deterministic

Use `deterministic` for setup, CI, demonstrations, failure injection, and
offline acceptance. It makes no network calls and requires no credentials.

```console
agentbus providers check deterministic
agentbus run --provider deterministic --workflow multi --durable "Create and verify a small calculator"
```

## Ollama

Ollama is the default local model provider. AgentBus does not install Ollama or
download models.

```powershell
$env:AGENTBUS_PROVIDER = "ollama"
$env:AGENTBUS_MODEL = "qwen2.5-coder:7b"
$env:AGENTBUS_OLLAMA_URL = "http://localhost:11434/api/generate"
agentbus providers check ollama
```

`providers check` validates local configuration. A live request requires the
explicit `--live` option.

## Azure OpenAI

Install `agentbus[azure]`, then provide the endpoint, API key, and deployment
through the launching process environment or an approved secure store. Never
write the key to an AgentBus config file.

```powershell
$env:AGENTBUS_PROVIDER = "azure"
$env:AZURE_OPENAI_ENDPOINT = "https://YOUR-RESOURCE.openai.azure.com"
$env:AZURE_OPENAI_API_KEY = "YOUR-KEY"
$env:AZURE_OPENAI_DEFAULT_DEPLOYMENT = "YOUR-DEPLOYMENT"
agentbus providers check azure
```

Only `agentbus providers check azure --live` sends a diagnostic request. Live
checks can incur cost and are never required by normal tests or release gates.
See the detailed [Azure guide](../providers/azure-openai.md).

## Fallback

Fallback is disabled by default. The only automatic route is explicit,
transient-failure Azure-to-Ollama fallback. Authentication, policy, schema,
approval, and safety failures never trigger fallback.

Inspect effective routing without revealing secret values:

```console
agentbus providers show --json
agentbus config explain provider_name --json
```
