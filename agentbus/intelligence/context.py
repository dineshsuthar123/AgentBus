from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from agentbus.intelligence.budgeting import ContextBudget
from agentbus.intelligence.discovery import RepositoryInventory
from agentbus.intelligence.errors import RepositoryIntelligenceError
from agentbus.intelligence.fingerprints import (
    content_hash,
    context_candidates_fingerprint,
)
from agentbus.intelligence.hybrid import HybridRetriever
from agentbus.intelligence.identities import (
    context_plan_id,
    stable_hash,
)
from agentbus.intelligence.models import (
    ContextCandidate,
    ContextPlan,
    ContextRole,
    IndexState,
    SearchQuery,
    SearchResult,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    _relative_path,
)
from agentbus.intelligence.selection import ContextSelector


_TASK_TERM = re.compile(
    r"`([^`]+)`|(?<!\w)([A-Za-z0-9_./:@-]{3,})(?!\w)"
)
_TASK_WORD = re.compile(r"[^\W_]{3,}", re.UNICODE)
_STOP_WORDS = {
    "add",
    "and",
    "change",
    "create",
    "fix",
    "for",
    "from",
    "implement",
    "into",
    "please",
    "the",
    "this",
    "update",
    "with",
}


@dataclass(frozen=True)
class ContextPlanningConfig:
    maximum_candidates: int = 500
    maximum_queries: int = 12
    results_per_query: int = 50
    maximum_source_bytes: int = 1_000_000
    maximum_candidate_characters: int = 90_000
    surrounding_lines: int = 12

    def __post_init__(self) -> None:
        _bounded(
            self.maximum_candidates,
            "maximum_candidates",
            1,
            2_000,
        )
        _bounded(self.maximum_queries, "maximum_queries", 1, 64)
        _bounded(
            self.results_per_query,
            "results_per_query",
            1,
            200,
        )
        _bounded(
            self.maximum_source_bytes,
            "maximum_source_bytes",
            1,
            10_000_000,
        )
        _bounded(
            self.maximum_candidate_characters,
            "maximum_candidate_characters",
            128,
            100_000,
        )
        _bounded(
            self.surrounding_lines,
            "surrounding_lines",
            0,
            200,
        )


@dataclass(frozen=True)
class ContextPlanningRequest:
    task: str
    role: ContextRole
    byte_budget: int
    token_budget: int
    snapshot_id: str | None = None
    index_state: IndexState = IndexState.CURRENT
    project_ids: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    task_graph: tuple[str, ...] = ()
    prior_attempts: tuple[str, ...] = ()
    tool_results: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task.strip() or len(self.task) > 20_000:
            raise ValueError(
                "context task must contain between 1 and 20000 characters"
            )
        ContextBudget(self.byte_budget, self.token_budget)
        for name, values, maximum in (
            ("project_ids", self.project_ids, 128),
            ("changed_paths", self.changed_paths, 1_000),
            ("task_graph", self.task_graph, 1_000),
            ("prior_attempts", self.prior_attempts, 256),
            ("tool_results", self.tool_results, 1_000),
        ):
            if len(values) > maximum:
                raise ValueError(f"{name} exceeds the configured item limit")
        for values in (
            self.task_graph,
            self.prior_attempts,
            self.tool_results,
        ):
            if any(len(item) > 8_192 for item in values):
                raise ValueError(
                    "context planning signal exceeds the character limit"
                )


@dataclass
class _CandidateSeed:
    source: SourceFile
    symbol: Symbol | None
    score: float
    reasons: set[str] = field(default_factory=set)

    @property
    def identity(self) -> str:
        return (
            self.symbol.symbol_id
            if self.symbol is not None
            else self.source.file_id
        )


class ContextPlanner:
    """Build role-specific, source-attributed context plans."""

    def __init__(
        self,
        inventory: RepositoryInventory,
        retriever: HybridRetriever,
        files: Iterable[SourceFile],
        symbols: Iterable[Symbol],
        *,
        config: ContextPlanningConfig | None = None,
        selector: ContextSelector | None = None,
    ) -> None:
        self.inventory = inventory
        self.retriever = retriever
        self.files = tuple(sorted(files, key=lambda item: item.file_id))
        self.symbols = tuple(
            sorted(symbols, key=lambda item: item.symbol_id)
        )
        self.config = config or ContextPlanningConfig()
        self.selector = selector or ContextSelector()
        self._files_by_path = {
            item.relative_path: item for item in self.files
        }
        self._inventory_files_by_path = {
            item.relative_path: item for item in self.inventory.files
        }
        self._symbols_by_id = {
            item.symbol_id: item for item in self.symbols
        }
        self._known_query_terms: dict[str, str] = {}
        for source in self.files:
            for value in (
                source.relative_path,
                source.relative_path.rsplit("/", 1)[-1],
                source.relative_path.rsplit("/", 1)[-1].rsplit(".", 1)[0],
            ):
                self._known_query_terms.setdefault(value.casefold(), value)
        for symbol in self.symbols:
            for value in (symbol.name, symbol.qualified_name):
                self._known_query_terms.setdefault(value.casefold(), value)
        self._symbols_by_file: dict[str, list[Symbol]] = {}
        for symbol in self.symbols:
            self._symbols_by_file.setdefault(symbol.file_id, []).append(symbol)

    def plan(self, request: ContextPlanningRequest) -> ContextPlan:
        normalized_changed = tuple(
            dict.fromkeys(
                _relative_path(path) for path in request.changed_paths
            )
        )
        project_filter = SearchQuery(
            text="context",
            project_ids=request.project_ids,
        ).project_ids
        task_hash = stable_hash(
            {
                "task": request.task,
                "role": request.role.value,
                "projects": project_filter,
                "changed_paths": normalized_changed,
                "task_graph": request.task_graph,
                "prior_attempts": request.prior_attempts,
                "tool_results": request.tool_results,
            }
        )
        seeds: dict[str, _CandidateSeed] = {}
        missing_changed = self._add_changed_seeds(
            seeds,
            normalized_changed,
            project_filter,
        )
        query_terms = self._queries(
            request.task,
            normalized_changed,
            signals=(
                *request.task_graph,
                *request.prior_attempts,
                *request.tool_results,
            ),
        )
        for term in query_terms:
            results = self.retriever.search(
                SearchQuery(
                    text=term,
                    project_ids=project_filter,
                    limit=self.config.results_per_query,
                ),
                stale=request.index_state != IndexState.CURRENT,
                recent_paths=normalized_changed,
            )
            for result in results:
                self._add_result_seed(seeds, result)
                if len(seeds) >= self.config.maximum_candidates:
                    break
            if len(seeds) >= self.config.maximum_candidates:
                break

        candidates: list[ContextCandidate] = []
        hash_mismatch = False
        for seed in sorted(
            seeds.values(),
            key=lambda item: (
                -self._role_score(item, request.role, normalized_changed),
                item.source.relative_path.casefold(),
                item.symbol.symbol_id if item.symbol is not None else "",
            ),
        )[: self.config.maximum_candidates]:
            candidate, mismatch = self._materialize(
                seed,
                request.role,
                normalized_changed,
            )
            candidates.append(candidate)
            hash_mismatch = hash_mismatch or mismatch

        selection = self.selector.select(
            tuple(candidates),
            ContextBudget(request.byte_budget, request.token_budget),
        )
        stale_warning = _stale_warning(
            request.index_state,
            source_hash_mismatch=hash_mismatch,
            changed_paths_missing=missing_changed,
        )
        plan_identity = context_plan_id(
            request.snapshot_id,
            task_hash,
            request.role.value,
            request.byte_budget,
            request.token_budget,
        )
        plan_hash = context_candidates_fingerprint(selection.candidates)
        return ContextPlan(
            plan_id=plan_identity,
            snapshot_id=request.snapshot_id,
            role=request.role,
            task_hash=task_hash,
            byte_budget=request.byte_budget,
            token_budget=request.token_budget,
            selected_bytes=selection.selected_bytes,
            selected_tokens=selection.selected_tokens,
            candidates=selection.candidates,
            stale_warning=stale_warning,
            plan_hash=plan_hash,
        )

    def _add_changed_seeds(
        self,
        seeds: dict[str, _CandidateSeed],
        changed_paths: tuple[str, ...],
        project_ids: tuple[str, ...],
    ) -> bool:
        missing = False
        for path in changed_paths:
            source = self._files_by_path.get(path)
            if (
                source is None
                or source.protected
                or (
                    project_ids
                    and source.project_id not in project_ids
                )
            ):
                missing = True
                continue
            seed = _CandidateSeed(
                source=source,
                symbol=None,
                score=250.0,
                reasons={"changed_file", "direct_source"},
            )
            seeds[seed.identity] = seed
        return missing

    def _add_result_seed(
        self,
        seeds: dict[str, _CandidateSeed],
        result: SearchResult,
    ) -> None:
        source = self._files_by_path.get(result.relative_path)
        if (
            source is None
            or source.protected
            or source.content_hash != result.source_hash
        ):
            return
        symbol = None
        if result.symbol is not None:
            symbol = self._symbols_by_id.get(result.symbol.symbol_id)
            if symbol is None or symbol.file_id != source.file_id:
                return
        identity = (
            symbol.symbol_id if symbol is not None else source.file_id
        )
        reasons = {"task_match"}
        reasons.update(result.score_components)
        if symbol is not None:
            reasons.add("definition")
            if symbol.documentation:
                reasons.add("documentation")
            if symbol.exported:
                reasons.add("public_api")
            if symbol.kind == SymbolKind.INTERFACE:
                reasons.add("interface")
            if symbol.kind == SymbolKind.CONFIGURATION_UNIT:
                reasons.add("configuration")
            if symbol.endpoint:
                reasons.add("endpoint")
            if symbol.test or symbol.kind == SymbolKind.TEST:
                reasons.add("test")
        if result.dependency_path:
            reasons.add("dependency_neighbor")
        current = seeds.get(identity)
        if current is None:
            seeds[identity] = _CandidateSeed(
                source=source,
                symbol=symbol,
                score=result.score,
                reasons=reasons,
            )
        else:
            current.score = max(current.score, result.score)
            current.reasons.update(reasons)

    def _queries(
        self,
        task: str,
        changed_paths: tuple[str, ...],
        *,
        signals: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        values: list[str] = []
        self._append_query_terms(values, task, known_only=False)
        if len(values) >= self.config.maximum_queries:
            return tuple(values[: self.config.maximum_queries])
        for signal in signals:
            self._append_query_terms(values, signal, known_only=True)
            if len(values) >= self.config.maximum_queries:
                return tuple(values[: self.config.maximum_queries])
        for path in changed_paths:
            source = self._files_by_path.get(path)
            if source is None:
                continue
            for symbol in self._symbols_by_file.get(source.file_id, ())[:4]:
                if symbol.name not in values:
                    values.append(symbol.name)
                    if len(values) >= self.config.maximum_queries:
                        return tuple(values)
        return tuple(values)

    def _append_query_terms(
        self,
        values: list[str],
        text: str,
        *,
        known_only: bool,
    ) -> None:
        extracted = [
            (match.group(1) or match.group(2) or "").strip()
            for match in _TASK_TERM.finditer(text)
        ]
        extracted.extend(match.group(0) for match in _TASK_WORD.finditer(text))
        for value in extracted:
            normalized = value.casefold()
            if not value or normalized in _STOP_WORDS:
                continue
            selected = (
                self._known_query_terms.get(normalized)
                if known_only
                else value
            )
            if selected is None or selected in values:
                continue
            values.append(selected)
            if len(values) >= self.config.maximum_queries:
                return

    def _materialize(
        self,
        seed: _CandidateSeed,
        role: ContextRole,
        changed_paths: tuple[str, ...],
    ) -> tuple[ContextCandidate, bool]:
        source = seed.source
        reasons = set(seed.reasons)
        if source.generated:
            reasons.add("generated_artifact")
        score = self._role_score(seed, role, changed_paths)
        content: str | None = None
        exclusion_reason: str | None = None
        mismatch = False
        if not self.inventory.contains(source.relative_path):
            exclusion_reason = "source_unavailable"
        else:
            discovered = self._inventory_files_by_path.get(
                source.relative_path
            )
            read_limit = min(
                self.config.maximum_source_bytes,
                self.inventory.limits.maximum_file_bytes,
            )
            if (
                discovered is None
                or discovered.size_bytes > read_limit
            ):
                exclusion_reason = "source_too_large"
            else:
                try:
                    full_content = self.inventory.read_text(
                        source.relative_path,
                        maximum_bytes=read_limit,
                    )
                except RepositoryIntelligenceError:
                    exclusion_reason = "source_unreadable"
                else:
                    if content_hash(full_content) != source.content_hash:
                        exclusion_reason = "source_hash_mismatch"
                        mismatch = True
                    else:
                        content = self._excerpt(full_content, seed.symbol)
                        if (
                            len(content)
                            > self.config.maximum_candidate_characters
                        ):
                            content = content[
                                : self.config.maximum_candidate_characters
                            ]
                            reasons.add("content_truncated")

        budget = ContextBudget(10_000_000, 2_000_000)
        cost = budget.measure(content or "")
        candidate_id = "candidate_" + stable_hash(
            {
                "path": source.relative_path,
                "source_hash": source.content_hash,
                "symbol_id": (
                    seed.symbol.symbol_id
                    if seed.symbol is not None
                    else None
                ),
                "role": role.value,
                "content_hash": (
                    stable_hash(content) if content is not None else None
                ),
            }
        )
        return (
            ContextCandidate(
                candidate_id=candidate_id,
                relative_path=source.relative_path,
                source_hash=source.content_hash,
                symbol_id=(
                    seed.symbol.symbol_id
                    if seed.symbol is not None
                    else None
                ),
                role=role,
                score=score,
                byte_count=cost.byte_count,
                estimated_tokens=cost.estimated_tokens,
                reasons=tuple(sorted(reasons)),
                exclusion_reason=exclusion_reason,
                content=content,
            ),
            mismatch,
        )

    def _excerpt(
        self,
        content: str,
        symbol: Symbol | None,
    ) -> str:
        if symbol is None:
            return content
        lines = content.splitlines(keepends=True)
        start = max(
            0,
            symbol.location.start_line - 1 - self.config.surrounding_lines,
        )
        end = min(
            len(lines),
            symbol.location.end_line + self.config.surrounding_lines,
        )
        return "".join(lines[start:end])

    @staticmethod
    def _role_score(
        seed: _CandidateSeed,
        role: ContextRole,
        changed_paths: tuple[str, ...],
    ) -> float:
        score = seed.score
        symbol = seed.symbol
        changed = seed.source.relative_path in changed_paths
        if changed:
            score += {
                ContextRole.PLANNER: 30.0,
                ContextRole.CODER: 50.0,
                ContextRole.VERIFIER: 25.0,
                ContextRole.REVIEWER: 50.0,
            }[role]
        if symbol is not None:
            if symbol.exported:
                score += {
                    ContextRole.PLANNER: 20.0,
                    ContextRole.CODER: 10.0,
                    ContextRole.VERIFIER: 10.0,
                    ContextRole.REVIEWER: 25.0,
                }[role]
            if symbol.test or symbol.kind == SymbolKind.TEST:
                score += {
                    ContextRole.PLANNER: 8.0,
                    ContextRole.CODER: 12.0,
                    ContextRole.VERIFIER: 40.0,
                    ContextRole.REVIEWER: 25.0,
                }[role]
            if (
                symbol.kind == SymbolKind.CONFIGURATION_UNIT
                or symbol.endpoint is not None
            ):
                score += {
                    ContextRole.PLANNER: 20.0,
                    ContextRole.CODER: 12.0,
                    ContextRole.VERIFIER: 20.0,
                    ContextRole.REVIEWER: 25.0,
                }[role]
            if symbol.kind == SymbolKind.INTERFACE:
                score += {
                    ContextRole.PLANNER: 20.0,
                    ContextRole.CODER: 18.0,
                    ContextRole.VERIFIER: 8.0,
                    ContextRole.REVIEWER: 20.0,
                }[role]
        if seed.source.language == SourceLanguage.MARKDOWN:
            score += 15.0 if role == ContextRole.PLANNER else 5.0
        if seed.source.generated:
            score *= 0.25
        return round(max(0.0, score), 6)


def _stale_warning(
    state: IndexState,
    *,
    source_hash_mismatch: bool,
    changed_paths_missing: bool,
) -> str | None:
    messages: list[str] = []
    if source_hash_mismatch:
        messages.append(
            "One or more source files no longer match the indexed hashes; "
            "mismatched content was excluded."
        )
    if changed_paths_missing:
        messages.append(
            "One or more changed files are not represented in the selected "
            "snapshot and were excluded."
        )
    if state != IndexState.CURRENT:
        messages.append(
            f"Repository intelligence state is {state.value}; verify selected "
            "context against the current workspace."
        )
    return " ".join(messages) if messages else None


def _bounded(
    value: int,
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
