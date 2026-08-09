from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentbus.config import AgentBusConfig
from agentbus.control.registry import DaemonRegistry, process_matches
from agentbus.execution.models import RunStatus
from agentbus.execution.state_store import StateStore
from agentbus.intelligence.service import RepositoryIntelligenceService
from agentbus.replay.service import TraceReplayService
from agentbus.security.redaction import redact_text
from agentbus.trace.retention import TraceRetentionPolicy


_RUN_LOG = re.compile(
    r"^\d{8}_\d{6}_(?P<run_id>[A-Za-z0-9][A-Za-z0-9._-]{0,127})\.jsonl$"
)
_TERMINAL_RUNS = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


class CleanupMode(StrEnum):
    NORMAL = "normal"
    STALE = "stale"
    ALL_RUNTIME_STATE = "all_runtime_state"


@dataclass(frozen=True)
class CleanupItem:
    category: str
    identifier: str
    location: str | None
    status: str
    reason: str
    affected_count: int = 1
    reclaimed_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "identifier": self.identifier,
            "location": self.location,
            "status": self.status,
            "reason": self.reason,
            "affected_count": self.affected_count,
            "reclaimed_bytes": self.reclaimed_bytes,
        }


@dataclass(frozen=True)
class CleanupResult:
    mode: CleanupMode
    dry_run: bool
    items: tuple[CleanupItem, ...]
    protected_data: tuple[str, ...]
    recommendations: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not any(item.status == "refused" for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return {
            "ok": self.ok,
            "mode": self.mode.value,
            "dry_run": self.dry_run,
            "items": [item.to_dict() for item in self.items],
            "counts": counts,
            "protected_data": list(self.protected_data),
            "recommendations": list(self.recommendations),
            "network_used": False,
        }


class RuntimeCleanup:
    def __init__(
        self,
        config: AgentBusConfig,
        *,
        registry_path: str | Path | None = None,
        now: datetime | None = None,
    ) -> None:
        self.config = config
        self.registry = DaemonRegistry(registry_path)
        self.now = now or datetime.now(UTC)
        if self.now.tzinfo is None:
            raise ValueError("Cleanup clock must include a timezone.")
        self.state_database = config.state_database_path.resolve()
        self.state_root = self.state_database.parent
        self.runs_root = Path(config.runs_dir).expanduser().resolve()
        self.index_database = self.state_root / "repository-index.sqlite3"

    def run(
        self,
        *,
        mode: CleanupMode | str = CleanupMode.NORMAL,
        dry_run: bool = False,
    ) -> CleanupResult:
        selected = CleanupMode(mode)
        items: list[CleanupItem] = []
        store = StateStore(self.state_database) if self.state_database.is_file() else None
        items.extend(self._clean_daemon_registry(dry_run=dry_run))
        if selected in {CleanupMode.STALE, CleanupMode.ALL_RUNTIME_STATE}:
            items.extend(
                self._clean_terminal_run_logs(
                    store,
                    mode=selected,
                    dry_run=dry_run,
                )
            )
        items.extend(self._clean_index(mode=selected, dry_run=dry_run))
        items.extend(self._clean_traces(store, mode=selected, dry_run=dry_run))
        return CleanupResult(
            mode=selected,
            dry_run=dry_run,
            items=tuple(items),
            protected_data=(
                "user repositories",
                "user and workspace configuration",
                "credentials and environment variables",
                "active runs and their logs",
                "active replay and trace dependencies",
                "user-created demo repositories",
            ),
            recommendations=(
                "Use `agentbus cleanup --stale` to include terminal run logs, "
                "stale worktrees, and terminal replay workspaces.",
                "Use `agentbus cleanup --all-runtime-state --yes` only when you "
                "intend to remove all safely identified terminal runtime artifacts.",
                "Package uninstall does not remove local configuration or runtime state.",
            ),
        )

    def _clean_daemon_registry(self, *, dry_run: bool) -> list[CleanupItem]:
        items: list[CleanupItem] = []
        for entry in self.registry.list():
            if process_matches(entry):
                items.append(
                    CleanupItem(
                        category="daemon_registry",
                        identifier=entry.daemon_id,
                        location=str(self.registry.path),
                        status="protected",
                        reason="The registered daemon process is active and identity-matched.",
                    )
                )
                continue
            if dry_run:
                status = "planned"
                reason = "Dead daemon registry entry would be removed."
            elif process_matches(entry):
                status = "protected"
                reason = "The daemon became active while cleanup was running."
            elif self.registry.remove(entry.daemon_id):
                status = "removed"
                reason = "Removed a dead daemon registry entry; no process was stopped."
            else:
                status = "skipped"
                reason = "The dead daemon entry had already been removed."
            items.append(
                CleanupItem(
                    category="daemon_registry",
                    identifier=entry.daemon_id,
                    location=str(self.registry.path),
                    status=status,
                    reason=reason,
                )
            )
        return items

    def _clean_terminal_run_logs(
        self,
        store: StateStore | None,
        *,
        mode: CleanupMode,
        dry_run: bool,
    ) -> list[CleanupItem]:
        if not self.runs_root.is_dir():
            return []
        runs = {run.run_id: run for run in store.list_runs()} if store else {}
        cutoff = self.now - timedelta(days=max(1, self.config.trace_retention_days))
        items: list[CleanupItem] = []
        for path in sorted(self.runs_root.iterdir(), key=lambda value: value.name):
            match = _RUN_LOG.fullmatch(path.name)
            if match is None or not path.is_file() or path.is_symlink():
                continue
            run_id = match.group("run_id")
            run = runs.get(run_id)
            if run is None:
                items.append(
                    CleanupItem(
                        category="run_log",
                        identifier=run_id,
                        location=str(path),
                        status="protected",
                        reason="No persisted run proves that this log is terminal.",
                    )
                )
                continue
            if run.status not in _TERMINAL_RUNS:
                items.append(
                    CleanupItem(
                        category="run_log",
                        identifier=run_id,
                        location=str(path),
                        status="protected",
                        reason=f"Run status is active: {run.status.value}.",
                    )
                )
                continue
            if mode != CleanupMode.ALL_RUNTIME_STATE and run.updated_at > cutoff:
                items.append(
                    CleanupItem(
                        category="run_log",
                        identifier=run_id,
                        location=str(path),
                        status="protected",
                        reason="Terminal run is still inside the retention window.",
                    )
                )
                continue
            if dry_run:
                status = "planned"
                reason = "Terminal run log would be removed."
            else:
                current = store.get_run(run_id) if store else None
                if current is None or current.status not in _TERMINAL_RUNS:
                    status = "protected"
                    reason = "Run is no longer proven terminal."
                else:
                    try:
                        reclaimed = path.stat().st_size
                        _unlink_direct_child(path, self.runs_root)
                    except OSError as exc:
                        items.append(
                            _refused("run_log", run_id, path, exc)
                        )
                        continue
                    status = "removed"
                    reason = "Removed a terminal run log after an active-state recheck."
                    items.append(
                        CleanupItem(
                            category="run_log",
                            identifier=run_id,
                            location=str(path),
                            status=status,
                            reason=reason,
                            reclaimed_bytes=reclaimed,
                        )
                    )
                    continue
            items.append(
                CleanupItem(
                    category="run_log",
                    identifier=run_id,
                    location=str(path),
                    status=status,
                    reason=reason,
                )
            )
        return items

    def _clean_index(
        self,
        *,
        mode: CleanupMode,
        dry_run: bool,
    ) -> list[CleanupItem]:
        if not self.index_database.is_file() or not self.config.workspace_path.is_dir():
            return []
        try:
            service = RepositoryIntelligenceService(
                self.config.workspace_path,
                self.index_database,
            )
            retain = 1 if mode == CleanupMode.ALL_RUNTIME_STATE else 3
            snapshots = service.store.list_snapshots(
                service.repository.repository_id,
                limit=1_000,
            )
            candidates = max(0, len(snapshots) - retain)
            if dry_run:
                return [
                    CleanupItem(
                        category="repository_index",
                        identifier=service.repository.repository_id,
                        location=str(self.index_database),
                        status="planned" if candidates else "skipped",
                        reason=(
                            f"Would retain {retain} snapshots and prune {candidates}."
                            if candidates
                            else "No old index snapshots require pruning."
                        ),
                        affected_count=candidates,
                    )
                ]
            report = service.garbage_collect(retain=retain, now=self.now)
            affected = report.deleted_snapshot_count + report.expired_cache_entries
            return [
                CleanupItem(
                    category="repository_index",
                    identifier=service.repository.repository_id,
                    location=str(self.index_database),
                    status="removed" if affected else "skipped",
                    reason=(
                        f"Pruned {report.deleted_snapshot_count} snapshots and "
                        f"{report.expired_cache_entries} expired cache entries."
                    ),
                    affected_count=affected,
                )
            ]
        except Exception as exc:
            return [_refused("repository_index", "current", self.index_database, exc)]

    def _clean_traces(
        self,
        store: StateStore | None,
        *,
        mode: CleanupMode,
        dry_run: bool,
    ) -> list[CleanupItem]:
        if store is None or not self.config.trace_store_path.is_dir():
            return []
        try:
            service = TraceReplayService(self.config, state_store=store)
            policy = TraceRetentionPolicy(
                keep_failures=True,
                keep_recent=(0 if mode == CleanupMode.ALL_RUNTIME_STATE else 100),
                keep_referenced=False,
                max_age_seconds=max(1, self.config.trace_retention_days) * 86_400,
            )
            plan = service.plan_gc(policy)
            if dry_run:
                return [
                    CleanupItem(
                        category="trace_objects",
                        identifier=plan.plan_id,
                        location=str(self.config.trace_store_path),
                        status="planned" if plan.deletion_hashes else "skipped",
                        reason=(
                            f"Would delete {len(plan.deletion_hashes)} expired objects; "
                            f"{len(plan.protected_hashes)} remain protected."
                        ),
                        affected_count=len(plan.deletion_hashes),
                        reclaimed_bytes=plan.reclaimable_bytes,
                    )
                ]
            report = service.execute_gc(plan)
            return [
                CleanupItem(
                    category="trace_objects",
                    identifier=report.plan_id,
                    location=str(self.config.trace_store_path),
                    status="removed" if report.deleted_objects else "skipped",
                    reason=(
                        f"Deleted {report.deleted_objects} expired objects; "
                        f"{report.skipped_objects} newly referenced objects were skipped."
                    ),
                    affected_count=report.deleted_objects,
                    reclaimed_bytes=report.reclaimed_bytes,
                )
            ]
        except Exception as exc:
            return [_refused("trace_objects", "retention", self.config.trace_store_path, exc)]


def _unlink_direct_child(path: Path, root: Path) -> None:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved.parent != resolved_root or path.is_symlink() or not path.is_file():
        raise OSError("Cleanup refused a run log outside its exact managed directory.")
    path.unlink()


def _refused(
    category: str,
    identifier: str,
    location: str | Path,
    error: BaseException,
) -> CleanupItem:
    detail = redact_text(str(error), max_chars=500) or type(error).__name__
    return CleanupItem(
        category=category,
        identifier=identifier,
        location=str(location),
        status="refused",
        reason=f"Cleanup refused this item safely: {detail}",
    )
