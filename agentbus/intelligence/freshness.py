from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentbus.intelligence.discovery import (
    DiscoveryLimits,
    RepositoryInventoryScanner,
)
from agentbus.intelligence.errors import (
    IndexCorruptedError,
    IndexSchemaError,
    RepositoryIntelligenceError,
)
from agentbus.intelligence.fingerprints import content_hash
from agentbus.intelligence.languages import source_language_for_path
from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    IndexOperationState,
    IndexState,
    IndexStatus,
    RepositoryIdentity,
    WorkspaceIdentity,
)
from agentbus.intelligence.parsers import (
    ParserRegistry,
    default_parser_registry,
)
from agentbus.intelligence.storage import IndexStore


@dataclass(frozen=True)
class FreshnessLimits:
    maximum_stale_paths: int = 1_000
    maximum_diagnostics: int = 1_000

    def __post_init__(self) -> None:
        if self.maximum_stale_paths < 1 or self.maximum_stale_paths > 1_000:
            raise ValueError(
                "maximum_stale_paths must be between 1 and 1000"
            )
        if self.maximum_diagnostics < 1 or self.maximum_diagnostics > 1_000:
            raise ValueError(
                "maximum_diagnostics must be between 1 and 1000"
            )


class IndexFreshnessChecker:
    """Validate a portable snapshot against contained local source state."""

    def __init__(
        self,
        workspace: str | Path,
        repository: RepositoryIdentity,
        workspace_identity: WorkspaceIdentity,
        store: IndexStore,
        *,
        registry: ParserRegistry | None = None,
        discovery_limits: DiscoveryLimits | None = None,
        limits: FreshnessLimits | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError("freshness workspace must be a directory")
        self.repository = RepositoryIdentity.model_validate(
            repository.model_dump(mode="python")
        )
        self.workspace_identity = WorkspaceIdentity.model_validate(
            workspace_identity.model_dump(mode="python")
        )
        if (
            self.workspace_identity.repository_id
            != self.repository.repository_id
        ):
            raise ValueError(
                "workspace and repository identities must refer to the same repository"
            )
        self.store = store
        self.registry = registry or default_parser_registry()
        self.discovery_limits = discovery_limits or DiscoveryLimits()
        self.limits = limits or FreshnessLimits()

    def status(self) -> IndexStatus:
        try:
            self.store.verify()
            operation = self.store.get_index_operation(
                self.repository.repository_id
            )
            snapshot = self.store.latest_snapshot(
                self.repository.repository_id
            )
        except IndexSchemaError:
            return self._terminal_status(
                IndexState.INCOMPATIBLE,
                "Repository index schema is incompatible.",
                "index.schema_incompatible",
            )
        except (IndexCorruptedError, RepositoryIntelligenceError):
            return self._terminal_status(
                IndexState.CORRUPTED,
                "Repository index could not be verified.",
                "index.corrupted",
            )

        if (
            operation is not None
            and operation.state == IndexOperationState.RUNNING
        ):
            return IndexStatus(
                repository_id=self.repository.repository_id,
                workspace_id=self.workspace_identity.workspace_id,
                state=IndexState.BUILDING,
                snapshot_id=(
                    snapshot.snapshot_id if snapshot is not None else None
                ),
                indexed_files=snapshot.file_count if snapshot else 0,
                total_files=snapshot.file_count if snapshot else 0,
                message="Repository indexing is in progress.",
            )
        if snapshot is None:
            return IndexStatus(
                repository_id=self.repository.repository_id,
                workspace_id=self.workspace_identity.workspace_id,
                state=IndexState.ABSENT,
                message="No repository index snapshot is available.",
            )
        if snapshot.workspace_id != self.workspace_identity.workspace_id:
            return self._snapshot_status(
                snapshot.snapshot_id,
                snapshot.file_count,
                IndexState.INCOMPATIBLE,
                "Repository index belongs to a different workspace scope.",
                (
                    IndexDiagnostic(
                        code="index.workspace_incompatible",
                        severity=DiagnosticSeverity.ERROR,
                        message=(
                            "Snapshot workspace identity does not match the "
                            "selected workspace."
                        ),
                        recoverable=True,
                    ),
                ),
            )
        if snapshot.parser_versions != self.registry.versions():
            return self._snapshot_status(
                snapshot.snapshot_id,
                snapshot.file_count,
                IndexState.INCOMPATIBLE,
                "Repository parser versions changed; rebuild is required.",
                (
                    IndexDiagnostic(
                        code="index.parsers_incompatible",
                        severity=DiagnosticSeverity.WARNING,
                        message=(
                            "Active parser versions differ from the indexed "
                            "snapshot."
                        ),
                        recoverable=True,
                    ),
                ),
            )

        inventory = RepositoryInventoryScanner(
            self.workspace,
            limits=self.discovery_limits,
        ).scan()
        indexed_files = {
            source.relative_path: source
            for source in self.store.list_files(snapshot.snapshot_id)
        }
        discovered_files = {
            item.relative_path: item
            for item in inventory.files
            if self._supports(item.relative_path)
        }
        stale_paths: set[str] = set()
        diagnostics = list(inventory.diagnostics)
        for path, discovered in discovered_files.items():
            indexed = indexed_files.get(path)
            if indexed is None:
                stale_paths.add(path)
                continue
            try:
                payload = inventory.read_bytes(
                    path,
                    maximum_bytes=min(
                        self.discovery_limits.maximum_file_bytes,
                        discovered.size_bytes + 1,
                    ),
                )
                current_hash = content_hash(payload.decode("utf-8-sig"))
            except (UnicodeError, RepositoryIntelligenceError):
                stale_paths.add(path)
                self._append_diagnostic(
                    diagnostics,
                    IndexDiagnostic(
                        code="index.freshness_read_failed",
                        severity=DiagnosticSeverity.WARNING,
                        message=(
                            "A source file could not be validated for freshness."
                        ),
                        relative_path=path,
                        recoverable=True,
                    ),
                )
                continue
            if current_hash != indexed.content_hash:
                stale_paths.add(path)
        if not inventory.truncated:
            stale_paths.update(
                set(indexed_files).difference(discovered_files)
            )

        ordered_stale = tuple(sorted(stale_paths))
        bounded_stale = ordered_stale[: self.limits.maximum_stale_paths]
        if len(bounded_stale) != len(ordered_stale):
            self._append_diagnostic(
                diagnostics,
                IndexDiagnostic(
                    code="index.stale_paths_truncated",
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        "Stale path reporting reached the configured limit."
                    ),
                    recoverable=True,
                    details={"stale_count": len(ordered_stale)},
                ),
            )

        state = self._state(
            snapshot.state,
            stale=bool(ordered_stale),
            inventory_truncated=inventory.truncated,
        )
        return IndexStatus(
            repository_id=self.repository.repository_id,
            workspace_id=self.workspace_identity.workspace_id,
            state=state,
            snapshot_id=snapshot.snapshot_id,
            stale_paths=bounded_stale,
            indexed_files=len(indexed_files),
            total_files=len(discovered_files),
            message=_status_message(state),
            diagnostics=tuple(
                diagnostics[: self.limits.maximum_diagnostics]
            ),
        )

    def _supports(self, relative_path: str) -> bool:
        language = source_language_for_path(relative_path)
        return bool(
            language is not None and self.registry.supports(language)
        )

    def _append_diagnostic(
        self,
        diagnostics: list[IndexDiagnostic],
        diagnostic: IndexDiagnostic,
    ) -> None:
        if len(diagnostics) < self.limits.maximum_diagnostics:
            diagnostics.append(diagnostic)

    def _terminal_status(
        self,
        state: IndexState,
        message: str,
        code: str,
    ) -> IndexStatus:
        return IndexStatus(
            repository_id=self.repository.repository_id,
            workspace_id=self.workspace_identity.workspace_id,
            state=state,
            message=message,
            diagnostics=(
                IndexDiagnostic(
                    code=code,
                    severity=DiagnosticSeverity.ERROR,
                    message=message,
                    recoverable=True,
                ),
            ),
        )

    def _snapshot_status(
        self,
        snapshot_id: str,
        file_count: int,
        state: IndexState,
        message: str,
        diagnostics: tuple[IndexDiagnostic, ...],
    ) -> IndexStatus:
        return IndexStatus(
            repository_id=self.repository.repository_id,
            workspace_id=self.workspace_identity.workspace_id,
            state=state,
            snapshot_id=snapshot_id,
            indexed_files=file_count,
            total_files=file_count,
            message=message,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _state(
        snapshot_state: IndexState,
        *,
        stale: bool,
        inventory_truncated: bool,
    ) -> IndexState:
        if snapshot_state in {
            IndexState.CORRUPTED,
            IndexState.INCOMPATIBLE,
            IndexState.PAUSED,
        }:
            return snapshot_state
        if stale:
            return IndexState.STALE
        if (
            inventory_truncated
            or snapshot_state == IndexState.PARTIALLY_CURRENT
        ):
            return IndexState.PARTIALLY_CURRENT
        return IndexState.CURRENT


def _status_message(state: IndexState) -> str:
    return {
        IndexState.CURRENT: "Repository index matches current source files.",
        IndexState.PARTIALLY_CURRENT: (
            "Repository index is usable with bounded completeness warnings."
        ),
        IndexState.STALE: "Repository source files changed after indexing.",
        IndexState.PAUSED: "Repository indexing is paused and can be resumed.",
        IndexState.CORRUPTED: "Repository index is corrupted.",
        IndexState.INCOMPATIBLE: "Repository index is incompatible.",
        IndexState.ABSENT: "No repository index snapshot is available.",
        IndexState.BUILDING: "Repository indexing is in progress.",
    }[state]
