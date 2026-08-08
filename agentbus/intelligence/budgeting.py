from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentCost:
    byte_count: int
    estimated_tokens: int


@dataclass(frozen=True)
class ContextBudget:
    byte_limit: int
    token_limit: int

    def __post_init__(self) -> None:
        if self.byte_limit < 1 or self.byte_limit > 10_000_000:
            raise ValueError("byte_limit must be between 1 and 10000000")
        if self.token_limit < 1 or self.token_limit > 2_000_000:
            raise ValueError("token_limit must be between 1 and 2000000")

    def measure(self, content: str) -> ContentCost:
        payload = content.encode("utf-8")
        return ContentCost(
            byte_count=len(payload),
            estimated_tokens=estimate_tokens(content),
        )

    def fits(
        self,
        current: ContentCost,
        additional: ContentCost,
    ) -> bool:
        return (
            current.byte_count + additional.byte_count
            <= self.byte_limit
            and current.estimated_tokens + additional.estimated_tokens
            <= self.token_limit
        )


def estimate_tokens(content: str) -> int:
    """Conservative dependency-free estimate for deterministic budgeting."""
    byte_count = len(content.encode("utf-8"))
    if byte_count == 0:
        return 0
    return (byte_count + 3) // 4
