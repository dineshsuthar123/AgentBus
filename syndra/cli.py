"""Canonical Syndra CLI backed by the compatibility-stable runtime."""

from agentbus.cli import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
