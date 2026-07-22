SCHEMA_VERSION = 4


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

CREATE TABLE IF NOT EXISTS cancellations (
    run_id TEXT PRIMARY KEY,
    requested INTEGER NOT NULL,
    requested_at TEXT,
    reason TEXT,
    propagated_at TEXT,
    propagation_sources_json TEXT NOT NULL,
    provider_cancellation_requested_at TEXT,
    provider_names_json TEXT NOT NULL,
    acknowledged INTEGER NOT NULL,
    acknowledged_at TEXT,
    acknowledgement_source TEXT,
    acknowledgement_stage TEXT,
    provider_cancellation_acknowledged_at TEXT,
    provider_acknowledgement_source TEXT,
    active_operations_json TEXT NOT NULL,
    operations_completed_after_request_json TEXT NOT NULL,
    tasks_prevented_from_starting_json TEXT NOT NULL,
    tasks_completed_after_request_json TEXT NOT NULL,
    scheduling_stopped_at TEXT,
    cleanup_completed_at TEXT,
    resume_eligible INTEGER NOT NULL,
    terminal_reason TEXT,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tool_invocations (
    invocation_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id TEXT NOT NULL UNIQUE,
    invocation_revision INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_version_json TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    caller_role TEXT NOT NULL,
    workspace_identity TEXT NOT NULL,
    worktree_identity TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    capability_fingerprint TEXT NOT NULL,
    arguments_sha256 TEXT NOT NULL,
    invocation_sha256 TEXT NOT NULL,
    operation_sha256 TEXT NOT NULL,
    idempotency_key_sha256 TEXT,
    status TEXT NOT NULL,
    resource_budget_json TEXT NOT NULL,
    anticipated_usage_json TEXT NOT NULL,
    resource_usage_json TEXT NOT NULL,
    process_slot INTEGER NOT NULL,
    policy_decision_json TEXT,
    approval_id TEXT,
    safe_result_json TEXT,
    cancellation_json TEXT NOT NULL,
    error_category TEXT,
    error_message TEXT,
    requested_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tool_approvals (
    approval_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id TEXT NOT NULL UNIQUE,
    invocation_id TEXT NOT NULL,
    invocation_revision INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    binding_sha256 TEXT,
    disposition TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    UNIQUE (invocation_id, invocation_revision),
    FOREIGN KEY (invocation_id) REFERENCES tool_invocations(invocation_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tool_audit_records (
    audit_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL UNIQUE,
    invocation_id TEXT NOT NULL,
    invocation_revision INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (invocation_id, invocation_revision),
    FOREIGN KEY (invocation_id) REFERENCES tool_invocations(invocation_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_worktrees_run ON worktrees(run_id, purpose, task_id);
CREATE INDEX IF NOT EXISTS idx_leases_task ON worker_leases(run_id, task_id, fencing_token);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_lease_per_task
    ON worker_leases(run_id, task_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_integration_run ON integration_attempts(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cancellations_requested
    ON cancellations(requested, updated_at);
CREATE INDEX IF NOT EXISTS idx_tool_invocations_run
    ON tool_invocations(run_id, invocation_sequence);
CREATE INDEX IF NOT EXISTS idx_tool_invocations_task
    ON tool_invocations(run_id, task_id, invocation_sequence);
CREATE INDEX IF NOT EXISTS idx_tool_invocations_status
    ON tool_invocations(run_id, status, invocation_sequence);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_invocations_idempotency
    ON tool_invocations(run_id, task_id, idempotency_key_sha256)
    WHERE idempotency_key_sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tool_approvals_run
    ON tool_approvals(run_id, approval_sequence);
CREATE INDEX IF NOT EXISTS idx_tool_audit_run
    ON tool_audit_records(run_id, audit_sequence);

CREATE TRIGGER IF NOT EXISTS tool_audit_records_no_update
BEFORE UPDATE ON tool_audit_records
BEGIN
    SELECT RAISE(ABORT, 'tool audit records are immutable');
END;

CREATE TRIGGER IF NOT EXISTS tool_audit_records_no_delete
BEFORE DELETE ON tool_audit_records
BEGIN
    SELECT RAISE(ABORT, 'tool audit records are immutable');
END;
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
    2: (
        """CREATE TABLE cancellations (
            run_id TEXT PRIMARY KEY,
            requested INTEGER NOT NULL,
            requested_at TEXT,
            reason TEXT,
            propagated_at TEXT,
            propagation_sources_json TEXT NOT NULL,
            provider_cancellation_requested_at TEXT,
            provider_names_json TEXT NOT NULL,
            acknowledged INTEGER NOT NULL,
            acknowledged_at TEXT,
            acknowledgement_source TEXT,
            acknowledgement_stage TEXT,
            provider_cancellation_acknowledged_at TEXT,
            provider_acknowledgement_source TEXT,
            active_operations_json TEXT NOT NULL,
            operations_completed_after_request_json TEXT NOT NULL,
            tasks_prevented_from_starting_json TEXT NOT NULL,
            tasks_completed_after_request_json TEXT NOT NULL,
            scheduling_stopped_at TEXT,
            cleanup_completed_at TEXT,
            resume_eligible INTEGER NOT NULL,
            terminal_reason TEXT,
            revision INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )""",
        "CREATE INDEX idx_cancellations_requested ON cancellations(requested, updated_at)",
    ),
    3: (
        """CREATE TABLE tool_invocations (
            invocation_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            invocation_id TEXT NOT NULL UNIQUE,
            invocation_revision INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            tool_version_json TEXT NOT NULL,
            protocol_version TEXT NOT NULL,
            caller_role TEXT NOT NULL,
            workspace_identity TEXT NOT NULL,
            worktree_identity TEXT NOT NULL,
            capabilities_json TEXT NOT NULL,
            capability_fingerprint TEXT NOT NULL,
            arguments_sha256 TEXT NOT NULL,
            invocation_sha256 TEXT NOT NULL,
            operation_sha256 TEXT NOT NULL,
            idempotency_key_sha256 TEXT,
            status TEXT NOT NULL,
            resource_budget_json TEXT NOT NULL,
            anticipated_usage_json TEXT NOT NULL,
            resource_usage_json TEXT NOT NULL,
            process_slot INTEGER NOT NULL,
            policy_decision_json TEXT,
            approval_id TEXT,
            safe_result_json TEXT,
            cancellation_json TEXT NOT NULL,
            error_category TEXT,
            error_message TEXT,
            requested_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (run_id, task_id)
                REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE tool_approvals (
            approval_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_id TEXT NOT NULL UNIQUE,
            invocation_id TEXT NOT NULL,
            invocation_revision INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            request_json TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            binding_sha256 TEXT,
            disposition TEXT,
            reason TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT,
            UNIQUE (invocation_id, invocation_revision),
            FOREIGN KEY (invocation_id)
                REFERENCES tool_invocations(invocation_id) ON DELETE CASCADE,
            FOREIGN KEY (run_id, task_id)
                REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE tool_audit_records (
            audit_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            audit_id TEXT NOT NULL UNIQUE,
            invocation_id TEXT NOT NULL,
            invocation_revision INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            record_sha256 TEXT NOT NULL,
            record_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (invocation_id, invocation_revision),
            FOREIGN KEY (invocation_id)
                REFERENCES tool_invocations(invocation_id) ON DELETE CASCADE,
            FOREIGN KEY (run_id, task_id)
                REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
        )""",
        "CREATE INDEX idx_tool_invocations_run ON tool_invocations(run_id, invocation_sequence)",
        "CREATE INDEX idx_tool_invocations_task ON tool_invocations(run_id, task_id, invocation_sequence)",
        "CREATE INDEX idx_tool_invocations_status ON tool_invocations(run_id, status, invocation_sequence)",
        """CREATE UNIQUE INDEX idx_tool_invocations_idempotency
            ON tool_invocations(run_id, task_id, idempotency_key_sha256)
            WHERE idempotency_key_sha256 IS NOT NULL""",
        "CREATE INDEX idx_tool_approvals_run ON tool_approvals(run_id, approval_sequence)",
        "CREATE INDEX idx_tool_audit_run ON tool_audit_records(run_id, audit_sequence)",
        """CREATE TRIGGER tool_audit_records_no_update
            BEFORE UPDATE ON tool_audit_records
            BEGIN
                SELECT RAISE(ABORT, 'tool audit records are immutable');
            END""",
        """CREATE TRIGGER tool_audit_records_no_delete
            BEFORE DELETE ON tool_audit_records
            BEGIN
                SELECT RAISE(ABORT, 'tool audit records are immutable');
            END""",
    ),
}
