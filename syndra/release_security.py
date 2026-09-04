"""Canonical Syndra release-security entry point."""

from agentbus.release_security import *  # noqa: F403
from agentbus.release_security import main


if __name__ == "__main__":
    raise SystemExit(main())
