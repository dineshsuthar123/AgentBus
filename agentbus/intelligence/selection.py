from __future__ import annotations

from dataclasses import dataclass

from agentbus.intelligence.budgeting import ContentCost, ContextBudget
from agentbus.intelligence.identities import stable_hash
from agentbus.intelligence.models import ContextCandidate


@dataclass(frozen=True)
class ContextSelection:
    candidates: tuple[ContextCandidate, ...]
    selected_bytes: int
    selected_tokens: int


class ContextSelector:
    """Greedily select stable whole candidates under both context budgets."""

    def select(
        self,
        candidates: tuple[ContextCandidate, ...],
        budget: ContextBudget,
    ) -> ContextSelection:
        ordered = sorted(
            candidates,
            key=lambda item: (
                -item.score,
                item.byte_count,
                item.relative_path.casefold(),
                item.symbol_id or "",
                item.candidate_id,
            ),
        )
        selected_cost = ContentCost(byte_count=0, estimated_tokens=0)
        seen_ids: set[str] = set()
        seen_content: set[str] = set()
        planned: list[ContextCandidate] = []
        for candidate in ordered:
            if candidate.candidate_id in seen_ids:
                planned.append(
                    _updated(
                        candidate,
                        selected=False,
                        exclusion_reason="duplicate_candidate",
                    )
                )
                continue
            seen_ids.add(candidate.candidate_id)
            if candidate.exclusion_reason is not None or candidate.content is None:
                planned.append(
                    _updated(
                        candidate,
                        selected=False,
                        exclusion_reason=(
                            candidate.exclusion_reason
                            or "content_unavailable"
                        ),
                    )
                )
                continue
            content_identity = stable_hash(candidate.content)
            if content_identity in seen_content:
                planned.append(
                    _updated(
                        candidate,
                        selected=False,
                        exclusion_reason="duplicate_content",
                    )
                )
                continue
            cost = budget.measure(candidate.content)
            candidate = _updated(
                candidate,
                selected=False,
                exclusion_reason=None,
                byte_count=cost.byte_count,
                estimated_tokens=cost.estimated_tokens,
            )
            if not budget.fits(selected_cost, cost):
                planned.append(
                    _updated(
                        candidate,
                        selected=False,
                        exclusion_reason="budget_exceeded",
                    )
                )
                continue
            seen_content.add(content_identity)
            selected_cost = ContentCost(
                byte_count=selected_cost.byte_count + cost.byte_count,
                estimated_tokens=(
                    selected_cost.estimated_tokens
                    + cost.estimated_tokens
                ),
            )
            planned.append(
                _updated(
                    candidate,
                    selected=True,
                    exclusion_reason=None,
                )
            )
        return ContextSelection(
            candidates=tuple(planned),
            selected_bytes=selected_cost.byte_count,
            selected_tokens=selected_cost.estimated_tokens,
        )


def _updated(
    candidate: ContextCandidate,
    *,
    selected: bool,
    exclusion_reason: str | None,
    byte_count: int | None = None,
    estimated_tokens: int | None = None,
) -> ContextCandidate:
    payload = candidate.model_dump(mode="python")
    payload.update(
        {
            "selected": selected,
            "exclusion_reason": exclusion_reason,
            "byte_count": (
                candidate.byte_count
                if byte_count is None
                else byte_count
            ),
            "estimated_tokens": (
                candidate.estimated_tokens
                if estimated_tokens is None
                else estimated_tokens
            ),
        }
    )
    return ContextCandidate.model_validate(payload)
