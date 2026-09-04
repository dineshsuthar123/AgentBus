"""Canonical Syndra package-audit entry point."""

from agentbus.release_packaging import *  # noqa: F403
from agentbus.release_packaging import main


if __name__ == "__main__":
    raise SystemExit(main())
