from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SchemaMigration:
    version: int
    name: str
    sql: str


MIGRATIONS = (
    SchemaMigration(
        version=1,
        name="repository_projects_and_snapshots",
        sql="""
CREATE TABLE IF NOT EXISTS repositories (
    repository_id TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE CHECK(length(key_hash) = 64),
    display_name TEXT CHECK(display_name IS NULL OR length(display_name) <= 256),
    schema_version INTEGER NOT NULL CHECK(schema_version >= 1),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id) ON DELETE CASCADE,
    roots_json TEXT NOT NULL CHECK(length(roots_json) <= 65536),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS index_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    file_count INTEGER NOT NULL DEFAULT 0 CHECK(file_count >= 0),
    symbol_count INTEGER NOT NULL DEFAULT 0 CHECK(symbol_count >= 0),
    reference_count INTEGER NOT NULL DEFAULT 0 CHECK(reference_count >= 0),
    edge_count INTEGER NOT NULL DEFAULT 0 CHECK(edge_count >= 0),
    project_map_hash TEXT NOT NULL CHECK(length(project_map_hash) = 64),
    graph_hash TEXT NOT NULL CHECK(length(graph_hash) = 64),
    parser_versions_json TEXT NOT NULL CHECK(length(parser_versions_json) <= 65536),
    source_fingerprint TEXT NOT NULL CHECK(length(source_fingerprint) = 64)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_repository_created
ON index_snapshots(repository_id, created_at DESC);

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT NOT NULL,
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL REFERENCES index_snapshots(snapshot_id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 256),
    kind TEXT NOT NULL,
    root TEXT NOT NULL CHECK(length(root) <= 2048),
    source_roots_json TEXT NOT NULL CHECK(length(source_roots_json) <= 65536),
    test_roots_json TEXT NOT NULL CHECK(length(test_roots_json) <= 65536),
    generated_roots_json TEXT NOT NULL CHECK(length(generated_roots_json) <= 65536),
    manifest_paths_json TEXT NOT NULL CHECK(length(manifest_paths_json) <= 65536),
    workspace_project_ids_json TEXT NOT NULL CHECK(length(workspace_project_ids_json) <= 65536),
    PRIMARY KEY(snapshot_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_projects_repository_root
ON projects(repository_id, root);

CREATE TABLE IF NOT EXISTS content_hashes (
    content_hash TEXT PRIMARY KEY CHECK(length(content_hash) = 64),
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS indexed_files (
    file_id TEXT NOT NULL,
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id) ON DELETE CASCADE,
    project_id TEXT,
    snapshot_id TEXT NOT NULL REFERENCES index_snapshots(snapshot_id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL CHECK(length(relative_path) BETWEEN 1 AND 2048),
    language TEXT NOT NULL,
    content_hash TEXT NOT NULL REFERENCES content_hashes(content_hash),
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    parser_name TEXT NOT NULL CHECK(length(parser_name) BETWEEN 1 AND 128),
    parser_version TEXT NOT NULL CHECK(length(parser_version) BETWEEN 1 AND 128),
    generated INTEGER NOT NULL DEFAULT 0 CHECK(generated IN (0, 1)),
    is_test INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0, 1)),
    protected INTEGER NOT NULL DEFAULT 0 CHECK(protected IN (0, 1)),
    PRIMARY KEY(snapshot_id, file_id),
    UNIQUE(repository_id, snapshot_id, relative_path),
    FOREIGN KEY(snapshot_id, project_id)
        REFERENCES projects(snapshot_id, project_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_files_repository_path
ON indexed_files(repository_id, relative_path);

CREATE INDEX IF NOT EXISTS idx_files_snapshot_hash
ON indexed_files(snapshot_id, content_hash);
""",
    ),
    SchemaMigration(
        version=2,
        name="symbols_edges_and_search",
        sql="""
CREATE TABLE IF NOT EXISTS modules (
    module_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES index_snapshots(snapshot_id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 512),
    qualified_name TEXT NOT NULL CHECK(length(qualified_name) BETWEEN 1 AND 2048),
    relative_path TEXT NOT NULL CHECK(length(relative_path) BETWEEN 1 AND 2048),
    language TEXT NOT NULL,
    public INTEGER NOT NULL DEFAULT 0 CHECK(public IN (0, 1)),
    PRIMARY KEY(snapshot_id, module_id),
    FOREIGN KEY(snapshot_id, project_id)
        REFERENCES projects(snapshot_id, project_id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS symbols (
    symbol_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    project_id TEXT,
    module_id TEXT,
    snapshot_id TEXT NOT NULL REFERENCES index_snapshots(snapshot_id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 512),
    qualified_name TEXT NOT NULL CHECK(length(qualified_name) BETWEEN 1 AND 2048),
    kind TEXT NOT NULL,
    language TEXT NOT NULL,
    start_line INTEGER NOT NULL CHECK(start_line >= 1),
    start_column INTEGER NOT NULL CHECK(start_column >= 0),
    end_line INTEGER NOT NULL CHECK(end_line >= 1),
    end_column INTEGER NOT NULL CHECK(end_column >= 0),
    signature TEXT CHECK(signature IS NULL OR length(signature) <= 4096),
    documentation TEXT CHECK(documentation IS NULL OR length(documentation) <= 8192),
    parent_symbol_id TEXT,
    exported INTEGER NOT NULL DEFAULT 0 CHECK(exported IN (0, 1)),
    is_test INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0, 1)),
    endpoint TEXT CHECK(endpoint IS NULL OR length(endpoint) <= 2048),
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    attributes_json TEXT NOT NULL CHECK(length(attributes_json) <= 65536),
    PRIMARY KEY(snapshot_id, symbol_id),
    FOREIGN KEY(snapshot_id, file_id)
        REFERENCES indexed_files(snapshot_id, file_id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(snapshot_id, project_id)
        REFERENCES projects(snapshot_id, project_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(snapshot_id, module_id)
        REFERENCES modules(snapshot_id, module_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(snapshot_id, parent_symbol_id)
        REFERENCES symbols(snapshot_id, symbol_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_symbols_snapshot_qualified
ON symbols(snapshot_id, qualified_name);

CREATE INDEX IF NOT EXISTS idx_symbols_file_kind
ON symbols(file_id, kind);

CREATE TABLE IF NOT EXISTS symbol_references (
    reference_id TEXT NOT NULL,
    source_symbol_id TEXT,
    source_file_id TEXT NOT NULL,
    target_symbol_id TEXT,
    snapshot_id TEXT NOT NULL REFERENCES index_snapshots(snapshot_id) ON DELETE CASCADE,
    unresolved_target TEXT CHECK(unresolved_target IS NULL OR length(unresolved_target) <= 2048),
    kind TEXT NOT NULL,
    relative_path TEXT NOT NULL CHECK(length(relative_path) BETWEEN 1 AND 2048),
    start_line INTEGER NOT NULL CHECK(start_line >= 1),
    start_column INTEGER NOT NULL CHECK(start_column >= 0),
    end_line INTEGER NOT NULL CHECK(end_line >= 1),
    end_column INTEGER NOT NULL CHECK(end_column >= 0),
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    explanation TEXT NOT NULL CHECK(length(explanation) BETWEEN 1 AND 2048),
    PRIMARY KEY(snapshot_id, reference_id),
    CHECK(target_symbol_id IS NOT NULL OR unresolved_target IS NOT NULL),
    FOREIGN KEY(snapshot_id, source_symbol_id)
        REFERENCES symbols(snapshot_id, symbol_id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(snapshot_id, source_file_id)
        REFERENCES indexed_files(snapshot_id, file_id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(snapshot_id, target_symbol_id)
        REFERENCES symbols(snapshot_id, symbol_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_references_target
ON symbol_references(snapshot_id, target_symbol_id);

CREATE TABLE IF NOT EXISTS dependency_edges (
    edge_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES index_snapshots(snapshot_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    source_id TEXT NOT NULL CHECK(length(source_id) BETWEEN 1 AND 256),
    target_id TEXT NOT NULL CHECK(length(target_id) BETWEEN 1 AND 256),
    relative_path TEXT CHECK(relative_path IS NULL OR length(relative_path) <= 2048),
    start_line INTEGER CHECK(start_line IS NULL OR start_line >= 1),
    start_column INTEGER CHECK(start_column IS NULL OR start_column >= 0),
    end_line INTEGER CHECK(end_line IS NULL OR end_line >= 1),
    end_column INTEGER CHECK(end_column IS NULL OR end_column >= 0),
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    parser_name TEXT NOT NULL CHECK(length(parser_name) BETWEEN 1 AND 128),
    parser_version TEXT NOT NULL CHECK(length(parser_version) BETWEEN 1 AND 128),
    explanation TEXT NOT NULL CHECK(length(explanation) BETWEEN 1 AND 2048),
    resolved INTEGER NOT NULL DEFAULT 1 CHECK(resolved IN (0, 1)),
    PRIMARY KEY(snapshot_id, edge_id)
);

CREATE INDEX IF NOT EXISTS idx_edges_source_kind
ON dependency_edges(snapshot_id, source_id, kind);

CREATE INDEX IF NOT EXISTS idx_edges_target_kind
ON dependency_edges(snapshot_id, target_id, kind);

CREATE TABLE IF NOT EXISTS ownership_rules (
    rule_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES index_snapshots(snapshot_id) ON DELETE CASCADE,
    pattern TEXT NOT NULL CHECK(length(pattern) BETWEEN 1 AND 2048),
    owners_json TEXT NOT NULL CHECK(length(owners_json) <= 32768),
    source_path TEXT NOT NULL CHECK(length(source_path) BETWEEN 1 AND 2048),
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    explanation TEXT NOT NULL CHECK(length(explanation) BETWEEN 1 AND 2048),
    PRIMARY KEY(snapshot_id, rule_id)
);

CREATE TABLE IF NOT EXISTS architecture_boundaries (
    boundary_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES index_snapshots(snapshot_id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 512),
    scope_json TEXT NOT NULL CHECK(length(scope_json) <= 65536),
    boundary_type TEXT NOT NULL,
    source_evidence_json TEXT NOT NULL CHECK(length(source_evidence_json) <= 65536),
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    explanation TEXT NOT NULL CHECK(length(explanation) BETWEEN 1 AND 2048),
    forbidden_targets_json TEXT NOT NULL CHECK(length(forbidden_targets_json) <= 65536),
    PRIMARY KEY(snapshot_id, boundary_id)
);

CREATE TABLE IF NOT EXISTS search_terms (
    term_id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL REFERENCES index_snapshots(snapshot_id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL CHECK(length(entity_type) BETWEEN 1 AND 32),
    entity_id TEXT NOT NULL CHECK(length(entity_id) BETWEEN 1 AND 256),
    field_name TEXT NOT NULL CHECK(length(field_name) BETWEEN 1 AND 64),
    normalized_text TEXT NOT NULL CHECK(length(normalized_text) BETWEEN 1 AND 8192),
    relative_path TEXT CHECK(relative_path IS NULL OR length(relative_path) <= 2048),
    weight REAL NOT NULL CHECK(weight >= 0),
    UNIQUE(snapshot_id, entity_type, entity_id, field_name, normalized_text)
);

CREATE INDEX IF NOT EXISTS idx_search_terms_snapshot_text
ON search_terms(snapshot_id, normalized_text);
""",
    ),
    SchemaMigration(
        version=3,
        name="diagnostics_invalidation_and_fencing",
        sql="""
CREATE TABLE IF NOT EXISTS index_diagnostics (
    diagnostic_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL REFERENCES index_snapshots(snapshot_id) ON DELETE CASCADE,
    code TEXT NOT NULL CHECK(length(code) BETWEEN 1 AND 128),
    severity TEXT NOT NULL,
    message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 2048),
    relative_path TEXT CHECK(relative_path IS NULL OR length(relative_path) <= 2048),
    parser_name TEXT CHECK(parser_name IS NULL OR length(parser_name) <= 128),
    recoverable INTEGER NOT NULL DEFAULT 1 CHECK(recoverable IN (0, 1)),
    details_json TEXT NOT NULL CHECK(length(details_json) <= 32768)
);

CREATE INDEX IF NOT EXISTS idx_diagnostics_snapshot_severity
ON index_diagnostics(snapshot_id, severity);

CREATE TABLE IF NOT EXISTS invalidation_state (
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL CHECK(length(relative_path) BETWEEN 1 AND 2048),
    reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 256),
    source_hash TEXT CHECK(source_hash IS NULL OR length(source_hash) = 64),
    detected_at TEXT NOT NULL,
    PRIMARY KEY(repository_id, relative_path)
);

CREATE TABLE IF NOT EXISTS index_operations (
    repository_id TEXT PRIMARY KEY REFERENCES repositories(repository_id) ON DELETE CASCADE,
    operation_id TEXT NOT NULL UNIQUE CHECK(length(operation_id) BETWEEN 1 AND 128),
    operation_kind TEXT NOT NULL CHECK(length(operation_kind) BETWEEN 1 AND 64),
    state TEXT NOT NULL CHECK(length(state) BETWEEN 1 AND 64),
    owner_pid INTEGER NOT NULL CHECK(owner_pid >= 1),
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    cancellation_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancellation_requested IN (0, 1))
);

CREATE TABLE IF NOT EXISTS intelligence_cache (
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id) ON DELETE CASCADE,
    namespace TEXT NOT NULL CHECK(length(namespace) BETWEEN 1 AND 64),
    cache_key TEXT NOT NULL CHECK(length(cache_key) BETWEEN 1 AND 256),
    value_hash TEXT NOT NULL CHECK(length(value_hash) = 64),
    metadata_json TEXT NOT NULL CHECK(length(metadata_json) <= 65536),
    expires_at TEXT,
    PRIMARY KEY(repository_id, namespace, cache_key)
);
""",
    ),
)


LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version
