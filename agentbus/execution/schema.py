SCHEMA_VERSION = 2


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    original_task TEXT NOT NULL,
    workflow_type TEXT NOT NULL,
    status TEXT NOT NULL,
    model TEXT NOT NULL,
    workspace TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    planner_output_json TEXT NOT NULL,
    context_summary TEXT,
    failure_reason TEXT,
    version INTEGER NOT NULL,
    graph_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    verifier_status TEXT,
    reviewer_status TEXT,
    changed_files_json TEXT NOT NULL,
    commit_identifier TEXT,
    pr_url TEXT,
    finalization_error TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    risk TEXT NOT NULL,
    assigned_role TEXT NOT NULL,
    dependencies_json TEXT NOT NULL,
    maximum_attempts INTEGER NOT NULL,
    current_attempt_count INTEGER NOT NULL,
    expected_outputs_json TEXT NOT NULL,
    done_criteria_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, task_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_category TEXT,
    error_message TEXT,
    observation_summary TEXT,
    metadata_json TEXT NOT NULL,
    UNIQUE (run_id, task_id, attempt_number),
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT,
    artifact_type TEXT NOT NULL,
    identifier TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    task_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_updated_at ON runs(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_run_position ON tasks(run_id, position);
CREATE INDEX IF NOT EXISTS idx_attempts_task ON attempts(run_id, task_id, attempt_number);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, event_id);
CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(run_id, task_id, approval_id);

CREATE TABLE IF NOT EXISTS worktrees (
    worktree_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT,
    path TEXT NOT NULL UNIQUE,
    repository_root TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    branch_ref TEXT NOT NULL UNIQUE,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL,
    worker_id TEXT,
    result_commit TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS worker_leases (
    lease_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    status TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    fencing_token INTEGER NOT NULL,
    metadata_json TEXT NOT NULL,
    UNIQUE (run_id, task_id, fencing_token),
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_commits (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL UNIQUE,
    parent_sha TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    changed_files_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, task_id),
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE,
    FOREIGN KEY (worktree_id) REFERENCES worktrees(worktree_id)
);

CREATE TABLE IF NOT EXISTS integration_attempts (
    integration_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_commit TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    resulting_commit TEXT,
    status TEXT NOT NULL,
    conflict_files_json TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_worktrees_run ON worktrees(run_id, purpose, task_id);
CREATE INDEX IF NOT EXISTS idx_leases_task ON worker_leases(run_id, task_id, fencing_token);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_lease_per_task
    ON worker_leases(run_id, task_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_integration_run ON integration_attempts(run_id, created_at);
"""


MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """CREATE TABLE worktrees (
            worktree_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, task_id TEXT,
            path TEXT NOT NULL UNIQUE, repository_root TEXT NOT NULL,
            base_commit TEXT NOT NULL, branch_ref TEXT NOT NULL UNIQUE,
            purpose TEXT NOT NULL, status TEXT NOT NULL, worker_id TEXT,
            result_commit TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE worker_leases (
            lease_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, task_id TEXT NOT NULL,
            worker_id TEXT NOT NULL, status TEXT NOT NULL, acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL, released_at TEXT,
            fencing_token INTEGER NOT NULL, metadata_json TEXT NOT NULL,
            UNIQUE (run_id, task_id, fencing_token),
            FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE task_commits (
            run_id TEXT NOT NULL, task_id TEXT NOT NULL, commit_sha TEXT NOT NULL UNIQUE,
            parent_sha TEXT NOT NULL, worktree_id TEXT NOT NULL,
            changed_files_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, task_id),
            FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE,
            FOREIGN KEY (worktree_id) REFERENCES worktrees(worktree_id)
        )""",
        """CREATE TABLE integration_attempts (
            integration_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, task_id TEXT NOT NULL,
            task_commit TEXT NOT NULL, base_commit TEXT NOT NULL, resulting_commit TEXT,
            status TEXT NOT NULL, conflict_files_json TEXT NOT NULL, error_message TEXT,
            created_at TEXT NOT NULL, completed_at TEXT,
            FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
        )""",
        "CREATE INDEX idx_worktrees_run ON worktrees(run_id, purpose, task_id)",
        "CREATE INDEX idx_leases_task ON worker_leases(run_id, task_id, fencing_token)",
        "CREATE UNIQUE INDEX idx_one_active_lease_per_task ON worker_leases(run_id, task_id) WHERE status = 'active'",
        "CREATE INDEX idx_integration_run ON integration_attempts(run_id, created_at)",
    ),
}
