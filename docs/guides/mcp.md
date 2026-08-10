# Local MCP integration

MCP is optional and disabled until servers are configured explicitly. AgentBus
supports managed stdio and authenticated numeric-loopback HTTP transports. It
does not discover public servers or trust advertised tools automatically.

Each imported tool needs an exact local capability map. MCP calls pass through
the same policy, approval, budget, timeout, cancellation, redaction, and audit
pipeline as built-in tools.

Store server configuration in a trusted user config when possible. Do not put
bearer tokens, API keys, shell command strings, or repository secrets in the
configuration. Use environment references or approved secure storage for
credentials.

After configuring a local server, run offline diagnostics:

```console
agentbus doctor --workspace . --verbose --json
agentbus config get mcp_server_configs --json
```

Treat MCP peers and their outputs as untrusted even after protocol and schema
validation. Run them with least-privilege OS credentials.

See the complete [MCP Integration](../mcp-integration.md) guide.
