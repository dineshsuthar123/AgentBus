/* Generated from protocol/agentbus-v1.schema.json. Do not edit. */

export const CONTROL_PROTOCOL_VERSION = "1.0" as const;

export interface ApprovalDecisionRequest {
  "revision": number;
  "reason"?: string | null;
}

export interface ApprovalDecisionResponse {
  "approval": ApprovalSummary;
  "idempotent"?: boolean;
}

export interface ApprovalListResponse {
  "run_id": string;
  "approvals": Array<ApprovalSummary>;
}

export interface ApprovalSummary {
  "approval_id": string;
  "run_id": string;
  "task_id": string;
  "risk_category": string;
  "reason"?: string | null;
  "requested_action": string;
  "affected_paths"?: Array<string>;
  "command"?: Array<string> | null;
  "created_at": string;
  "state": string;
  "revision"?: number;
}

export interface CancelResponse {
  "run_id": string;
  "status": string;
  "cancellation_requested": boolean;
  "cancellation"?: CancellationLifecycle;
}

export interface CancellationLifecycle {
  "requested"?: boolean;
  "requested_at"?: string | null;
  "reason"?: string | null;
  "propagated_at"?: string | null;
  "propagation_sources"?: Array<string>;
  "provider_cancellation_signalled"?: boolean;
  "provider_cancellation_requested_at"?: string | null;
  "provider_names"?: Array<string>;
  "provider_cancellation_acknowledged"?: boolean;
  "provider_cancellation_acknowledged_at"?: string | null;
  "provider_acknowledgement_source"?: string | null;
  "acknowledged"?: boolean;
  "acknowledged_at"?: string | null;
  "acknowledgement_source"?: string | null;
  "acknowledgement_stage"?: string | null;
  "active_non_interruptible_operation"?: string | null;
  "active_non_interruptible_operations"?: Array<string>;
  "operations_completed_after_request"?: Array<string>;
  "completed_after_cancellation_request"?: boolean;
  "tasks_prevented_from_starting"?: Array<string>;
  "tasks_completed_after_request"?: Array<string>;
  "scheduling_stopped"?: boolean;
  "scheduling_stopped_at"?: string | null;
  "cleanup_completed"?: boolean;
  "cleanup_completed_at"?: string | null;
  "resume_eligible"?: boolean;
  "terminal_reason"?: string | null;
  "revision"?: number;
}

export interface ChangeListResponse {
  "run_id": string;
  "workspace": string;
  "changes": Array<ChangeSummary>;
  "truncated"?: boolean;
}

export interface ChangeSummary {
  "path": string;
  "status": string;
  "tracked": boolean;
  "additions"?: number | null;
  "deletions"?: number | null;
  "binary"?: boolean;
  "generated"?: boolean;
  "generated_reason"?: string | null;
  "classification": string;
  "task_id"?: string | null;
}

export interface DaemonRegistryEntry {
  "daemon_id": string;
  "pid": number;
  "executable": string;
  "process_start_identity": string;
  "host": string;
  "port": number;
  "protocol_version"?: string;
  "agentbus_version": string;
  "started_at": string;
  "heartbeat_at": string;
  "state_database": string;
  "registry_path": string;
}

export interface DeterministicProviderOptions {
  "profile"?: "python-calculator" | "cancellation-two-task";
  "latency_seconds"?: number;
  "latency_roles"?: Array<"planner" | "coder" | "reviewer" | "summarizer">;
  "failure_kind"?: "output_error" | "timeout" | "service_unavailable";
  "failure_calls"?: Array<number>;
  "failure_roles"?: Array<"planner" | "coder" | "reviewer" | "summarizer">;
}

export interface DiffResponse {
  "run_id": string;
  "path"?: string | null;
  "diff": string;
  "truncated"?: boolean;
  "byte_limit": number;
}

export interface DoctorResponse {
  "status": string;
  "checks": Array<Record<string, unknown>>;
}

export interface ErrorBody {
  "code": string;
  "message": string;
  "retryable"?: boolean;
  "details"?: Record<string, unknown>;
}

export interface ErrorResponse {
  "error": ErrorBody;
}

export interface EventEnvelope {
  "sequence": number;
  "event_type": string;
  "timestamp": string;
  "run_id"?: string | null;
  "task_id"?: string | null;
  "worker_id"?: string | null;
  "payload"?: Record<string, unknown>;
}

export interface EventListResponse {
  "events": Array<EventEnvelope>;
  "last_sequence"?: number;
}

export interface FileContentResponse {
  "run_id": string;
  "path": string;
  "content": string;
  "revision": "before" | "after";
  "truncated"?: boolean;
}

export interface HealthResponse {
  "status"?: "ok";
  "protocol_version"?: string;
}

export interface InfoResponse {
  "protocol_version"?: string;
  "agentbus_version": string;
  "daemon_id": string;
  "pid": number;
  "host": string;
  "port": number;
  "started_at": string;
  "state_database": string;
  "capabilities"?: Array<string>;
}

export interface ProviderCheckRequest {
  "provider": "ollama" | "azure" | "deterministic";
  "live_consent"?: boolean;
}

export interface ProviderListResponse {
  "providers": Array<ProviderSummary>;
}

export interface ProviderSummary {
  "name": string;
  "configured": boolean;
  "ready": boolean;
  "model"?: string | null;
  "endpoint_host"?: string | null;
  "message"?: string | null;
}

export interface ReadyHandshake {
  "protocol_version"?: string;
  "host": string;
  "port": number;
  "daemon_id": string;
  "pid": number;
  "agentbus_version": string;
  "registry_path": string;
  "bearer_token": string;
  "token_delivery"?: "parent_process_stdout";
}

export interface ResumeResponse {
  "run_id": string;
  "status": string;
  "resumed": boolean;
}

export interface RoleModelOverrides {
  "planner"?: string | null;
  "coder"?: string | null;
  "reviewer"?: string | null;
  "summarizer"?: string | null;
}

export interface RunAcceptedResponse {
  "run_id": string;
  "status": string;
  "workspace": string;
  "created_at": string;
}

export interface RunActionRequest {
  "reason"?: string | null;
}

export interface RunCreateRequest {
  "task": string;
  "workspace": string;
  "provider"?: "ollama" | "azure" | "deterministic";
  "fallback_provider"?: "ollama" | "azure" | "deterministic" | null;
  "workflow"?: WorkflowMode;
  "durable"?: boolean;
  "parallel"?: boolean;
  "max_workers"?: number;
  "role_models"?: RoleModelOverrides;
  "deterministic"?: DeterministicProviderOptions;
  "fallback_enabled"?: boolean;
  "live_provider_consent"?: boolean;
  "create_pr"?: boolean;
  "commit_changes"?: boolean;
  "keep_worktrees"?: boolean;
  "retry_limit"?: number;
  "tags"?: Array<string>;
  "metadata"?: Record<string, unknown>;
}

export interface RunListResponse {
  "runs": Array<RunSummary>;
  "total": number;
}

export interface RunReportResponse {
  "run_id": string;
  "status": string;
  "report": Record<string, unknown>;
  "cancellation"?: CancellationLifecycle;
}

export interface RunSummary {
  "run_id": string;
  "status": string;
  "workflow": string;
  "workspace": string;
  "original_task": string;
  "created_at": string;
  "updated_at": string;
  "completed_at"?: string | null;
  "verifier_status"?: string | null;
  "reviewer_status"?: string | null;
  "failure_reason"?: string | null;
  "changed_files"?: Array<string>;
  "version": number;
  "cancellation"?: CancellationLifecycle;
}

export interface SchedulerResponse {
  "run_id": string;
  "configured_max_workers": number;
  "parallel_enabled": boolean;
  "workers_used"?: Array<string>;
  "current_leases"?: Array<Record<string, unknown>>;
  "expired_leases"?: Array<Record<string, unknown>>;
  "integration_order"?: Array<string>;
  "integration_conflicts"?: Array<Record<string, unknown>>;
  "cancellation"?: CancellationLifecycle;
}

export interface TaskListResponse {
  "run_id": string;
  "tasks": Array<TaskSummary>;
}

export interface TaskSummary {
  "task_id": string;
  "title": string;
  "description": string;
  "status": string;
  "position": number;
  "dependencies"?: Array<string>;
  "expected_outputs"?: Array<string>;
  "done_criteria"?: Array<string>;
  "assigned_role": string;
  "risk": string;
  "attempts": number;
  "worker_id"?: string | null;
  "provider"?: string | null;
  "model"?: string | null;
  "failure_category"?: string | null;
  "failure_message"?: string | null;
  "verifier_status"?: string | null;
  "reviewer_status"?: string | null;
  "created_at": string;
  "updated_at": string;
}

export interface UsageResponse {
  "run_id": string;
  "requests"?: number;
  "input_tokens"?: number;
  "output_tokens"?: number;
  "total_tokens"?: number;
  "retries"?: number;
  "fallbacks"?: number;
  "latency_seconds"?: number;
  "routes"?: Array<Record<string, unknown>>;
}

export type WorkflowMode = "single" | "multi";

export interface WorkspaceValidationRequest {
  "workspace": string;
  "require_git"?: boolean;
}

export interface WorkspaceValidationResponse {
  "valid": boolean;
  "workspace": string;
  "git_top_level"?: string | null;
  "is_git_repository"?: boolean;
  "message"?: string | null;
}

export interface WorktreeListResponse {
  "run_id": string;
  "worktrees": Array<WorktreeSummary>;
}

export interface WorktreeSummary {
  "worktree_id": string;
  "task_id"?: string | null;
  "path": string;
  "branch"?: string | null;
  "status": string;
  "retained"?: boolean;
}
