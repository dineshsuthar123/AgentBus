# Provider troubleshooting

Start with a local-only check:

```console
agentbus providers list
agentbus providers show --json
agentbus providers check deterministic
agentbus providers check ollama
agentbus providers check azure
```

These checks do not contact a provider unless `--live` is explicit.

## Ollama

Confirm Ollama is running, the configured model was downloaded by you, and
`AGENTBUS_OLLAMA_URL` is an HTTP(S) URL with a hostname. AgentBus does not
automatically download or switch models.

## Azure

Verify endpoint, authentication mode, API mode, and role/default deployment
names. `401` usually indicates authentication, `403` authorization, `404` a
deployment or endpoint mismatch, and `429` rate or quota pressure. AgentBus
does not retry permanent configuration or authorization failures.

Use `agentbus providers check azure --live` only when one paid diagnostic call
is approved. Never include keys or raw provider responses in support material.

See [Providers](../guides/providers.md) and
[Azure OpenAI](../providers/azure-openai.md).
