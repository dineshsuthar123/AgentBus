"""Syndra public package metadata and compatibility-safe API."""

from agentbus._version import __version__
from agentbus.config import AgentBusConfig as SyndraConfig

__all__ = ["SyndraConfig", "__version__"]
