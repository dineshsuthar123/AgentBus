class McpError(RuntimeError):
    """Base error for the constrained AgentBus MCP boundary."""


class McpTransportError(McpError):
    """Raised when an MCP transport cannot safely continue."""


class McpProtocolError(McpError):
    """Raised when a peer sends an invalid MCP or JSON-RPC message."""


class McpOutputLimitExceeded(McpTransportError):
    """Raised when an MCP peer exceeds a configured output bound."""


class McpRequestTimeout(McpTransportError):
    """Raised when an MCP request exceeds its configured deadline."""


class McpRemoteError(McpProtocolError):
    """Raised for a bounded JSON-RPC error returned by an MCP peer."""


class McpUnsupportedProtocolVersion(McpProtocolError):
    """Raised when the peer selects a protocol revision we do not support."""


class McpToolImportError(McpProtocolError):
    """Raised when a remote tool cannot become a safe managed tool."""
