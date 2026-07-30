from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from agentbus.intelligence.discovery import (
    DiscoveryLimits,
    ProjectDiscovery,
    RepositoryInventory,
    RepositoryInventoryScanner,
)
from agentbus.intelligence.errors import (
    IndexUnavailableError,
    RepositoryIntelligenceError,
)
from agentbus.intelligence.fingerprints import (
    content_hash,
    file_set_fingerprint,
    graph_fingerprint,
    parser_versions_fingerprint,
    project_map_fingerprint,
)
from agentbus.intelligence.identities import (
    file_id,
    module_id,
    reference_id,
    snapshot_id,
    stable_hash,
    symbol_id,
)
from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    IndexSnapshot,
    IndexState,
    Module,
    Project,
    ProjectKind,
    RepositoryIdentity,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolReference,
    WorkspaceIdentity,
)
from agentbus.intelligence.parsers import (
    CancellationSignal,
    GoStaticParser,
    JavaStaticParser,
    ParseRequest,
    ParseResult,
    ParserLimits,
    ParserRegistry,
    PythonAstParser,
    TypeScriptStaticParser,
)
from agentbus.intelligence.storage import IndexStore


_LANGUAGE_BY_SUFFIX = {
    ".cjs": SourceLanguage.JAVASCRIPT,
    ".cts": SourceLanguage.TYPESCRIPT,
    ".go": SourceLanguage.GO,
    ".java": SourceLanguage.JAVA,
    ".js": SourceLanguage.JAVASCRIPT,
    ".jsx": SourceLanguage.JAVASCRIPT,
    ".mjs": SourceLanguage.JAVASCRIPT,
    ".mts": SourceLanguage.TYPESCRIPT,
    ".py": SourceLanguage.PYTHON,
    ".ts": SourceLanguage.TYPESCRIPT,
    ".tsx": SourceLanguage.TYPESCRIPT,
}
_PROJECT_KIND_BY_LANGUAGE = {
    SourceLanguage.GO: ProjectKind.GO,
    SourceLanguage.JAVA: ProjectKind.JAVA,
    SourceLanguage.JAVASCRIPT: ProjectKind.NODE,
    SourceLanguage.PYTHON: ProjectKind.PYTHON,
    SourceLanguage.TYPESCRIPT: ProjectKind.NODE,
}
_MAX_SNAPSHOT_DIAGNOSTICS = 1_000


@dataclass(frozen=True)
class IndexingResult:
    snapshot: IndexSnapshot
    indexed_paths: tuple[str, ...]
    reused_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    renamed_paths: tuple[tuple[str, str], ...] = ()
    unchanged: bool = False


@dataclass(frozen=True)
class _ParsedUnit:
    source: SourceFile
    result: ParseResult
    project: Project | None


@dataclass(frozen=True)
class _SymbolDraft:
    unit: _ParsedUnit
    definition_index: int
    identity: str
    module_identity: str | None


class RepositoryIndexer:
    """Build portable, content-addressed repository intelligence snapshots."""

    def __init__(
        self,
        workspace: str | Path,
        repository: RepositoryIdentity,
        workspace_identity: WorkspaceIdentity,
        store: IndexStore,
        *,
        registry: ParserRegistry | None = None,
        discovery_limits: DiscoveryLimits | None = None,
        parser_limits: ParserLimits | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise IndexUnavailableError(
                "Repository intelligence workspace is not a directory."
            )
        self.repository = RepositoryIdentity.model_validate(
            repository.model_dump(mode="python")
        )
        self.workspace_identity = WorkspaceIdentity.model_validate(
            workspace_identity.model_dump(mode="python")
        )
        if self.workspace_identity.repository_id != self.repository.repository_id:
            raise ValueError(
                "workspace and repository identities must refer to the same repository"
            )
        self.store = store
        self.registry = registry or _default_registry()
        self.discovery_limits = discovery_limits or DiscoveryLimits()
        self.parser_limits = parser_limits or ParserLimits(
            maximum_source_bytes=self.discovery_limits.maximum_file_bytes
        )

    def build(
        self,
        *,
        cancellation: CancellationSignal | None = None,
    ) -> IndexingResult:
        started_at = datetime.now(timezone.utc)
        inventory = RepositoryInventoryScanner(
            self.workspace,
            limits=self.discovery_limits,
        ).scan()
        discovery = ProjectDiscovery(
            self.workspace,
            self.repository,
            limits=self.discovery_limits,
        ).discover_inventory(inventory)
        projects = tuple(
            sorted(discovery.projects, key=lambda item: item.project_id)
        )
        parser_versions = self.registry.versions()
        project_hash = project_map_fingerprint(projects)
        configuration_hash, configuration_diagnostics = (
            _configuration_fingerprint(
                inventory,
                projects,
                self.discovery_limits,
            )
        )
        diagnostics = [
            IndexDiagnostic(
                code="index.configuration_fingerprint",
                severity=DiagnosticSeverity.INFO,
                message="Repository configuration fingerprint captured.",
                recoverable=True,
                details={"fingerprint": configuration_hash},
            ),
            *discovery.diagnostics,
            *configuration_diagnostics,
        ]
        previous = self.store.latest_snapshot(
            self.repository.repository_id
        )
        if (
            previous is not None
            and previous.workspace_id
            != self.workspace_identity.workspace_id
        ):
            previous = None
        previous_files = (
            self.store.list_files(previous.snapshot_id)
            if previous is not None
            else ()
        )
        previous_files_by_path = {
            source.relative_path: source
            for source in previous_files
        }
        reuse_enabled = bool(
            previous is not None
            and previous.state
            not in {IndexState.CORRUPTED, IndexState.INCOMPATIBLE}
            and previous.project_map_hash == project_hash
            and _snapshot_configuration_fingerprint(previous)
            == configuration_hash
        )
        units: list[_ParsedUnit] = []
        carried_sources: dict[str, SourceFile] = {}
        observed_hashes: dict[str, str] = {}
        indexed_paths: list[str] = []
        reused_paths: list[str] = []
        skipped_paths: list[str] = []
        processed_paths: set[str] = set()
        parser_partial = False
        paused = False
        supported_files = tuple(
            item
            for item in discovery.files
            if _source_language(item.relative_path) is not None
        )

        for discovered in supported_files:
            if _cancelled(cancellation):
                paused = True
                break
            processed_paths.add(discovered.relative_path)
            language = _source_language(discovered.relative_path)
            if language is None:
                continue
            project = _project_for_path(
                discovered.relative_path,
                language,
                discovery.projects,
            )
            try:
                payload = inventory.read_bytes(
                    discovered.relative_path,
                    maximum_bytes=min(
                        self.discovery_limits.maximum_file_bytes,
                        self.parser_limits.maximum_source_bytes,
                    ),
                )
                observed_hashes[discovered.relative_path] = content_hash(payload)
                content = payload.decode("utf-8-sig")
                parser = self.registry.resolve(language)
                descriptor = parser.descriptor
                source_identity = file_id(
                    self.repository.repository_id,
                    discovered.relative_path,
                )
                previous_source = previous_files_by_path.get(
                    discovered.relative_path
                )
                decoded_hash = content_hash(content)
                project_identity = project.project_id if project else None
                if (
                    reuse_enabled
                    and previous_source is not None
                    and previous_source.content_hash == decoded_hash
                    and previous_source.language == language
                    and previous_source.project_id == project_identity
                    and previous_source.parser_name == descriptor.name
                    and previous_source.parser_version == descriptor.version
                ):
                    carried_sources[discovered.relative_path] = (
                        previous_source.model_copy(
                            update={
                                "size_bytes": len(payload),
                                "generated": discovered.generated,
                                "test": discovered.test,
                            }
                        )
                    )
                    reused_paths.append(discovered.relative_path)
                    continue
                request = ParseRequest.from_content(
                    repository_id=self.repository.repository_id,
                    file_id=source_identity,
                    project_id=project.project_id if project else None,
                    relative_path=discovered.relative_path,
                    language=language,
                    content=content,
                )
                result = self.registry.parse(
                    request,
                    limits=self.parser_limits,
                    cancellation=cancellation,
                )
                source = SourceFile(
                    file_id=source_identity,
                    repository_id=self.repository.repository_id,
                    project_id=project.project_id if project else None,
                    relative_path=discovered.relative_path,
                    language=language,
                    content_hash=result.source_hash,
                    size_bytes=len(payload),
                    parser_name=descriptor.name,
                    parser_version=descriptor.version,
                    generated=discovered.generated,
                    test=discovered.test,
                    protected=False,
                )
                units.append(
                    _ParsedUnit(
                        source=source,
                        result=result,
                        project=project,
                    )
                )
                indexed_paths.append(discovered.relative_path)
                diagnostics.extend(result.diagnostics)
                parser_partial = parser_partial or result.partial
                if result.cancelled:
                    paused = True
                    break
            except (UnicodeError, RepositoryIntelligenceError, ValueError) as exc:
                skipped_paths.append(discovered.relative_path)
                parser_partial = True
                if reuse_enabled:
                    previous_source = previous_files_by_path.get(
                        discovered.relative_path
                    )
                    if previous_source is not None:
                        carried_sources[discovered.relative_path] = (
                            previous_source
                        )
                _append_diagnostic(
                    diagnostics,
                    IndexDiagnostic(
                        code="index.file_failed",
                        severity=DiagnosticSeverity.WARNING,
                        message="A source file could not be indexed safely.",
                        relative_path=discovered.relative_path,
                        recoverable=True,
                        details={"error_type": type(exc).__name__},
                    ),
                )

        if paused and reuse_enabled:
            discovered_paths = {
                item.relative_path for item in supported_files
            }
            for path, source in previous_files_by_path.items():
                if path in discovered_paths and path not in processed_paths:
                    carried_sources[path] = source

        new_modules, new_symbols, new_references = _materialize_records(units)
        reused_file_ids = {
            source.file_id for source in carried_sources.values()
        }
        previous_modules = (
            self.store.list_modules(previous.snapshot_id)
            if previous is not None and reused_file_ids
            else ()
        )
        previous_symbols = (
            self.store.list_symbols(previous.snapshot_id)
            if previous is not None and reused_file_ids
            else ()
        )
        previous_references = (
            self.store.list_references(previous.snapshot_id)
            if previous is not None and reused_file_ids
            else ()
        )
        reused_modules = tuple(
            item
            for item in previous_modules
            if item.relative_path in carried_sources
        )
        reused_symbols = tuple(
            item
            for item in previous_symbols
            if item.file_id in reused_file_ids
        )
        reused_references = tuple(
            item
            for item in previous_references
            if item.source_file_id in reused_file_ids
        )
        modules = _unique_modules((*reused_modules, *new_modules))
        symbols = _unique_symbols((*reused_symbols, *new_symbols))
        references = _rebind_references(
            (*reused_references, *new_references),
            symbols,
            modules,
            previous_symbols,
        )
        files = tuple(
            sorted(
                (
                    *carried_sources.values(),
                    *(unit.source for unit in units),
                ),
                key=lambda item: item.file_id,
            )
        )
        current_paths = {
            item.relative_path for item in supported_files
        }
        deleted_paths = sorted(
            set(previous_files_by_path).difference(current_paths)
        )
        renamed_paths = _detect_renames(
            deleted_paths,
            indexed_paths,
            previous_files_by_path,
            {source.relative_path: source for source in files},
        )
        renamed_sources = {source for source, _ in renamed_paths}
        deleted_paths = [
            path for path in deleted_paths if path not in renamed_sources
        ]
        source_fingerprint = stable_hash(
            {
                "inventory": discovery.inventory_fingerprint,
                "configuration": configuration_hash,
                "observed_sources": file_set_fingerprint(observed_hashes),
                "observation_complete": (
                    len(observed_hashes) == len(supported_files)
                ),
            }
        )
        project_hash = project_map_fingerprint(projects)
        graph_hash = graph_fingerprint(())
        state = _snapshot_state(
            paused=paused,
            partial=(
                parser_partial
                or discovery.truncated
                or bool(skipped_paths)
            ),
        )
        bounded_diagnostics = tuple(
            diagnostics[:_MAX_SNAPSHOT_DIAGNOSTICS]
        )
        identity_source = (
            source_fingerprint
            if state == IndexState.CURRENT
            else stable_hash(
                {
                    "source_fingerprint": source_fingerprint,
                    "state": state.value,
                    "diagnostics": [
                        {
                            "code": item.code,
                            "path": item.relative_path,
                        }
                        for item in bounded_diagnostics
                    ],
                }
            )
        )
        identity = snapshot_id(
            self.repository.repository_id,
            identity_source,
            parser_versions_fingerprint(parser_versions),
            project_hash,
            graph_hash,
        )
        completed_at = (
            None if state == IndexState.PAUSED else datetime.now(timezone.utc)
        )
        snapshot = IndexSnapshot(
            snapshot_id=identity,
            repository_id=self.repository.repository_id,
            workspace_id=self.workspace_identity.workspace_id,
            state=state,
            created_at=started_at,
            completed_at=completed_at,
            file_count=len(files),
            symbol_count=len(symbols),
            reference_count=len(references),
            edge_count=0,
            project_map_hash=project_hash,
            graph_hash=graph_hash,
            parser_versions=parser_versions,
            source_fingerprint=source_fingerprint,
            diagnostics=bounded_diagnostics,
        )

        try:
            existing = self.store.get_snapshot(identity)
        except IndexUnavailableError:
            stored = self.store.publish_snapshot(
                self.repository,
                self.workspace_identity,
                snapshot,
                projects=projects,
                files=files,
                modules=modules,
                symbols=symbols,
                references=references,
            )
            return IndexingResult(
                snapshot=stored,
                indexed_paths=tuple(sorted(indexed_paths)),
                reused_paths=tuple(sorted(reused_paths)),
                skipped_paths=tuple(sorted(skipped_paths)),
                deleted_paths=tuple(deleted_paths),
                renamed_paths=renamed_paths,
            )
        return IndexingResult(
            snapshot=existing,
            indexed_paths=tuple(sorted(indexed_paths)),
            reused_paths=tuple(sorted(reused_paths)),
            skipped_paths=tuple(sorted(skipped_paths)),
            deleted_paths=tuple(deleted_paths),
            renamed_paths=renamed_paths,
            unchanged=not any(
                (
                    indexed_paths,
                    skipped_paths,
                    deleted_paths,
                    renamed_paths,
                    paused,
                )
            ),
        )

    def update(
        self,
        *,
        cancellation: CancellationSignal | None = None,
    ) -> IndexingResult:
        return self.build(cancellation=cancellation)


def _default_registry() -> ParserRegistry:
    return ParserRegistry(
        (
            PythonAstParser(),
            TypeScriptStaticParser(),
            JavaStaticParser(),
            GoStaticParser(),
        )
    )


def _configuration_fingerprint(
    inventory: RepositoryInventory,
    projects: tuple[Project, ...],
    limits: DiscoveryLimits,
) -> tuple[str, tuple[IndexDiagnostic, ...]]:
    manifest_paths = sorted(
        {
            path
            for project in projects
            for path in project.manifest_paths
            if inventory.contains(path)
        }
    )
    hashes: dict[str, str] = {}
    diagnostics: list[IndexDiagnostic] = []
    for path in manifest_paths[:1_024]:
        try:
            payload = inventory.read_bytes(
                path,
                maximum_bytes=min(
                    limits.maximum_metadata_bytes,
                    limits.maximum_file_bytes,
                ),
            )
            hashes[path] = content_hash(payload)
        except RepositoryIntelligenceError as exc:
            _append_diagnostic(
                diagnostics,
                IndexDiagnostic(
                    code="index.configuration_unreadable",
                    severity=DiagnosticSeverity.WARNING,
                    message="A project configuration file could not be hashed.",
                    relative_path=path,
                    recoverable=True,
                    details={"error_type": type(exc).__name__},
                ),
            )
    return stable_hash(hashes), tuple(diagnostics)


def _snapshot_configuration_fingerprint(
    snapshot: IndexSnapshot,
) -> str | None:
    for diagnostic in snapshot.diagnostics:
        if diagnostic.code != "index.configuration_fingerprint":
            continue
        fingerprint = diagnostic.details.get("fingerprint")
        if isinstance(fingerprint, str):
            return fingerprint
    return None


def _unique_modules(modules: tuple[Module, ...]) -> tuple[Module, ...]:
    by_identity: dict[str, Module] = {}
    for module in modules:
        existing = by_identity.get(module.module_id)
        if existing is not None and existing != module:
            raise ValueError("incremental index produced conflicting modules")
        by_identity[module.module_id] = module
    return tuple(
        sorted(by_identity.values(), key=lambda item: item.module_id)
    )


def _unique_symbols(symbols: tuple[Symbol, ...]) -> tuple[Symbol, ...]:
    by_identity: dict[str, Symbol] = {}
    for symbol in symbols:
        existing = by_identity.get(symbol.symbol_id)
        if existing is not None and existing != symbol:
            raise ValueError("incremental index produced conflicting symbols")
        by_identity[symbol.symbol_id] = symbol
    return tuple(
        sorted(by_identity.values(), key=lambda item: item.symbol_id)
    )


def _rebind_references(
    references: tuple[SymbolReference, ...],
    symbols: tuple[Symbol, ...],
    modules: tuple[Module, ...],
    previous_symbols: tuple[Symbol, ...],
) -> tuple[SymbolReference, ...]:
    symbols_by_id = {item.symbol_id: item for item in symbols}
    previous_by_id = {
        item.symbol_id: item for item in previous_symbols
    }
    symbols_by_qualified_name: dict[str, list[str]] = defaultdict(list)
    for symbol in symbols:
        symbols_by_qualified_name[symbol.qualified_name].append(
            symbol.symbol_id
        )
    modules_by_id = {item.module_id: item for item in modules}
    rebound: dict[str, SymbolReference] = {}
    for reference in references:
        source = (
            symbols_by_id.get(reference.source_symbol_id)
            if reference.source_symbol_id
            else None
        )
        current_target = (
            symbols_by_id.get(reference.target_symbol_id)
            if reference.target_symbol_id
            else None
        )
        previous_target = (
            previous_by_id.get(reference.target_symbol_id)
            if reference.target_symbol_id
            else None
        )
        target = (
            reference.unresolved_target
            or (
                current_target.qualified_name
                if current_target is not None
                else None
            )
            or (
                previous_target.qualified_name
                if previous_target is not None
                else None
            )
        )
        if target is None:
            continue
        module = (
            modules_by_id.get(source.module_id)
            if source is not None and source.module_id
            else None
        )
        target_identity = _resolve_target(
            target,
            source.qualified_name if source else None,
            module,
            symbols_by_qualified_name,
        )
        rebound_reference = SymbolReference(
            reference_id=reference.reference_id,
            source_symbol_id=(
                source.symbol_id if source is not None else None
            ),
            source_file_id=reference.source_file_id,
            target_symbol_id=target_identity,
            unresolved_target=None if target_identity else target,
            kind=reference.kind,
            location=reference.location,
            confidence=reference.confidence,
            explanation=reference.explanation,
        )
        existing = rebound.get(reference.reference_id)
        if existing is not None and existing != rebound_reference:
            raise ValueError(
                "incremental index produced conflicting references"
            )
        rebound[reference.reference_id] = rebound_reference
    return tuple(
        sorted(rebound.values(), key=lambda item: item.reference_id)
    )


def _detect_renames(
    deleted_paths: list[str],
    indexed_paths: list[str],
    previous_files: dict[str, SourceFile],
    current_files: dict[str, SourceFile],
) -> tuple[tuple[str, str], ...]:
    added_paths = sorted(
        path for path in indexed_paths if path not in previous_files
    )
    available = set(deleted_paths)
    renames: list[tuple[str, str]] = []
    for target in added_paths:
        current = current_files.get(target)
        if current is None:
            continue
        source = next(
            (
                path
                for path in sorted(available)
                if previous_files[path].content_hash == current.content_hash
                and previous_files[path].language == current.language
            ),
            None,
        )
        if source is None:
            continue
        available.remove(source)
        renames.append((source, target))
    return tuple(renames)


def _materialize_records(
    units: list[_ParsedUnit],
) -> tuple[
    tuple[Module, ...],
    tuple[Symbol, ...],
    tuple[SymbolReference, ...],
]:
    modules: dict[str, Module] = {}
    drafts: list[_SymbolDraft] = []
    symbol_ids_by_file_name: dict[
        tuple[str, str],
        list[str],
    ] = defaultdict(list)
    symbols_by_qualified_name: dict[str, list[str]] = defaultdict(list)

    for unit in units:
        module = _module_for_unit(unit)
        if module is not None:
            modules[module.module_id] = module
        ordinals: dict[tuple[str, SymbolKind, str | None], int] = defaultdict(int)
        for index, definition in enumerate(unit.result.definitions):
            key = (
                definition.qualified_name,
                definition.kind,
                definition.signature,
            )
            ordinal = ordinals[key]
            ordinals[key] += 1
            identity = symbol_id(
                unit.source.file_id,
                definition.qualified_name,
                definition.kind,
                signature=definition.signature,
                ordinal=ordinal,
            )
            drafts.append(
                _SymbolDraft(
                    unit=unit,
                    definition_index=index,
                    identity=identity,
                    module_identity=module.module_id if module else None,
                )
            )
            symbol_ids_by_file_name[
                (unit.source.file_id, definition.qualified_name)
            ].append(identity)
            symbols_by_qualified_name[definition.qualified_name].append(identity)

    symbols: list[Symbol] = []
    for draft in drafts:
        definition = draft.unit.result.definitions[draft.definition_index]
        parent_candidates = symbol_ids_by_file_name.get(
            (
                draft.unit.source.file_id,
                definition.parent_qualified_name or "",
            ),
            (),
        )
        symbols.append(
            Symbol(
                symbol_id=draft.identity,
                file_id=draft.unit.source.file_id,
                project_id=draft.unit.source.project_id,
                module_id=draft.module_identity,
                name=definition.name,
                qualified_name=definition.qualified_name,
                kind=definition.kind,
                language=draft.unit.source.language,
                location=definition.location,
                signature=definition.signature,
                documentation=definition.documentation,
                parent_symbol_id=(
                    parent_candidates[0] if parent_candidates else None
                ),
                exported=definition.exported,
                test=definition.test,
                endpoint=definition.endpoint,
                confidence=definition.confidence,
                attributes=definition.attributes,
            )
        )

    references: dict[str, SymbolReference] = {}
    for unit in units:
        module = _module_for_unit(unit)
        for parsed in unit.result.references:
            source_candidates = symbol_ids_by_file_name.get(
                (
                    unit.source.file_id,
                    parsed.source_qualified_name or "",
                ),
                (),
            )
            target_identity = _resolve_target(
                parsed.target,
                parsed.source_qualified_name,
                module,
                symbols_by_qualified_name,
            )
            identity = reference_id(
                unit.source.file_id,
                parsed.location.relative_path,
                parsed.location.start_line,
                parsed.location.start_column,
                parsed.target,
                parsed.kind.value,
            )
            references.setdefault(
                identity,
                SymbolReference(
                    reference_id=identity,
                    source_symbol_id=(
                        source_candidates[0] if source_candidates else None
                    ),
                    source_file_id=unit.source.file_id,
                    target_symbol_id=target_identity,
                    unresolved_target=(
                        None if target_identity else parsed.target
                    ),
                    kind=parsed.kind,
                    location=parsed.location,
                    confidence=parsed.confidence,
                    explanation=parsed.explanation,
                ),
            )

    return (
        tuple(sorted(modules.values(), key=lambda item: item.module_id)),
        tuple(sorted(symbols, key=lambda item: item.symbol_id)),
        tuple(sorted(references.values(), key=lambda item: item.reference_id)),
    )


def _module_for_unit(unit: _ParsedUnit) -> Module | None:
    if unit.project is None:
        return None
    definition = next(
        (
            item
            for item in unit.result.definitions
            if item.kind in {SymbolKind.MODULE, SymbolKind.PACKAGE}
        ),
        None,
    )
    qualified_name = (
        definition.qualified_name
        if definition
        else PurePosixPath(unit.source.relative_path).with_suffix("").as_posix()
    )
    name = (
        definition.name
        if definition
        else PurePosixPath(unit.source.relative_path).stem
    )
    identity = module_id(
        unit.project.project_id,
        unit.source.relative_path,
        qualified_name,
    )
    return Module(
        module_id=identity,
        project_id=unit.project.project_id,
        name=name,
        qualified_name=qualified_name,
        relative_path=unit.source.relative_path,
        language=unit.source.language,
        public=bool(definition and definition.exported),
    )


def _resolve_target(
    target: str,
    source_qualified_name: str | None,
    module: Module | None,
    symbols_by_qualified_name: dict[str, list[str]],
) -> str | None:
    candidates = [target]
    if module is not None and not target.startswith("."):
        candidates.append(f"{module.qualified_name}.{target}")
    if source_qualified_name and not target.startswith("."):
        parts = source_qualified_name.split(".")
        for end in range(len(parts) - 1, 0, -1):
            candidates.append(f"{'.'.join(parts[:end])}.{target}")
    for candidate in dict.fromkeys(candidates):
        identities = symbols_by_qualified_name.get(candidate, ())
        if len(identities) == 1:
            return identities[0]
    return None


def _source_language(relative_path: str) -> SourceLanguage | None:
    return _LANGUAGE_BY_SUFFIX.get(PurePosixPath(relative_path).suffix.lower())


def _project_for_path(
    relative_path: str,
    language: SourceLanguage,
    projects: tuple[Project, ...],
) -> Project | None:
    expected = _PROJECT_KIND_BY_LANGUAGE[language]
    candidates = [
        project
        for project in projects
        if project.kind in {expected, ProjectKind.GENERIC}
        and _contains_path(project.root, relative_path)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            -len(PurePosixPath(item.root).parts) if item.root else 0,
            item.kind != expected,
            item.project_id,
        ),
    )


def _contains_path(root: str, relative_path: str) -> bool:
    if not root:
        return True
    return relative_path == root or relative_path.startswith(f"{root}/")


def _snapshot_state(*, paused: bool, partial: bool) -> IndexState:
    if paused:
        return IndexState.PAUSED
    if partial:
        return IndexState.PARTIALLY_CURRENT
    return IndexState.CURRENT


def _append_diagnostic(
    diagnostics: list[IndexDiagnostic],
    diagnostic: IndexDiagnostic,
) -> None:
    if len(diagnostics) < _MAX_SNAPSHOT_DIAGNOSTICS:
        diagnostics.append(diagnostic)


def _cancelled(cancellation: CancellationSignal | None) -> bool:
    return bool(cancellation is not None and cancellation.is_set())
