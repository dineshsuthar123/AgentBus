"""Canonical module entry point for the offline evaluation harness."""

from agentbus.eval import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
