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
  "approval_kind"?: "task" | "tool";
  "tool_name"?: string | null;
  "tool_version"?: ToolVersion | null;
  "capabilities"?: Array<ToolCapability>;
  "arguments_summary"?: Array<string>;
  "executable"?: string | null;
  "working_directory"?: string | null;
  "network_destination"?: string | null;
  "policy_rule"?: string | null;
  "proposed_constraints"?: Array<ToolCapability>;
  "resource_budget"?: ToolResourceBudget | null;
  "expires_at"?: string | null;
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

export interface CapabilityScope {
  "roots"?: Array<string>;
  "patterns"?: Array<string>;
  "affected_paths"?: Array<string>;
  "executables"?: Array<string>;
  "working_directories"?: Array<string>;
  "network_allowed"?: boolean;
  "network_destinations"?: Array<string>;
  "environment_keys"?: Array<string>;
  "git_operations"?: Array<string>;
  "mcp_servers"?: Array<string>;
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
  "profile"?: "python-calculator" | "cancellation-two-task" | "tool-safe-read" | "tool-atomic-write" | "tool-source-patch" | "tool-pytest" | "tool-git-diff" | "tool-git-commit" | "tool-delete-approval" | "tool-deny-outside-read" | "tool-deny-credential-read" | "tool-process-timeout" | "tool-process-cancel" | "tool-excessive-output" | "tool-budget-exhaustion" | "tool-local-mcp" | "tool-loop-limit";
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
  "tool_budget"?: ToolResourceBudget;
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

export interface ToolArtifact {
  "artifact_id": string;
  "kind": ToolArtifactKind;
  "relative_path"?: string | null;
  "media_type"?: string;
  "size_bytes": number;
  "sha256": string;
  "truncated"?: boolean;
  "safe_metadata"?: Record<string, unknown>;
}

export type ToolArtifactKind = "file" | "diff" | "report" | "test_result" | "process_output" | "metadata";

export interface ToolAuditEntryResponse {
  "audit_sequence": number;
  "record": ToolAuditRecord;
}

export interface ToolAuditListResponse {
  "run_id": string;
  "records": Array<ToolAuditEntryResponse>;
  "after_sequence"?: number;
  "next_sequence"?: number;
  "truncated"?: boolean;
}

export interface ToolAuditRecord {
  "audit_id": string;
  "invocation_id": string;
  "invocation_revision": number;
  "run_id": string;
  "task_id": string;
  "tool_name": string;
  "tool_version": ToolVersion;
  "protocol_version"?: string;
  "caller_role": string;
  "capabilities": Array<ToolCapability>;
  "policy_decision": ToolPolicyDecision;
  "approval_id"?: string | null;
  "arguments_sha256": string;
  "affected_resource_hashes"?: Record<string, string>;
  "started_at"?: string | null;
  "completed_at"?: string | null;
  "cancellation"?: ToolCancellationSnapshot;
  "timed_out"?: boolean;
  "resource_usage"?: ToolResourceUsage;
  "artifacts"?: Array<ToolArtifact>;
  "outcome": ToolInvocationStatus;
  "error_category"?: ToolErrorCategory | null;
  "created_at"?: string;
}

export interface ToolCancellationSnapshot {
  "requested"?: boolean;
  "revision"?: number;
  "requested_at"?: string | null;
  "signal_sent"?: boolean;
  "acknowledged"?: boolean;
  "process_terminated"?: boolean;
  "operation_completed_after_request"?: boolean;
  "cleanup_completed"?: boolean;
  "reason"?: string | null;
}

export interface ToolCapability {
  "name": ToolCapabilityName;
  "scope"?: CapabilityScope;
}

export type ToolCapabilityName = "filesystem.read" | "filesystem.write" | "filesystem.create" | "filesystem.delete" | "filesystem.rename" | "process.execute" | "process.network" | "git.read" | "git.write" | "git.commit" | "git.branch" | "git.worktree" | "test.execute" | "package.install" | "environment.read_safe" | "mcp.connect" | "mcp.invoke";

export interface ToolDescriptorDetail {
  "name": string;
  "version": ToolVersion;
  "protocol_version": string;
  "description": string;
  "capabilities": Array<ToolCapability>;
  "safety": string;
  "idempotent": boolean;
  "supports_cancellation": boolean;
  "maximum_timeout_seconds": number;
  "argument_schema": Record<string, unknown>;
  "output_schema": Record<string, unknown>;
}

export interface ToolDescriptorSummary {
  "name": string;
  "version": ToolVersion;
  "protocol_version": string;
  "description": string;
  "capabilities": Array<ToolCapability>;
  "safety": string;
  "idempotent": boolean;
  "supports_cancellation": boolean;
  "maximum_timeout_seconds": number;
}

export interface ToolError {
  "category": ToolErrorCategory;
  "code": string;
  "message": string;
  "retryable"?: boolean;
  "safe_metadata"?: Record<string, unknown>;
}

export type ToolErrorCategory = "validation" | "policy_denied" | "approval_required" | "approval_invalid" | "cancelled" | "timed_out" | "resource_exhausted" | "filesystem" | "process" | "git" | "mcp" | "protocol" | "internal";

export interface ToolInvocationCancelRequest {
  "reason"?: string | null;
}

export interface ToolInvocationCancelResponse {
  "run_id": string;
  "invocation_id": string;
  "invocation_status": string;
  "run_cancellation_requested": boolean;
  "cancellation"?: CancellationLifecycle;
}

export interface ToolInvocationDetail {
  "invocation_sequence": number;
  "invocation_id": string;
  "invocation_revision": number;
  "run_id": string;
  "task_id": string;
  "tool_name": string;
  "tool_version": ToolVersion;
  "protocol_version": string;
  "caller_role": string;
  "status": string;
  "capabilities": Array<ToolCapability>;
  "policy_decision"?: ToolPolicyDecision | null;
  "approval_id"?: string | null;
  "resource_budget": ToolResourceBudget;
  "resource_usage": ToolResourceUsage;
  "cancellation": ToolCancellationSnapshot;
  "error_category"?: string | null;
  "error_message"?: string | null;
  "requested_at": string;
  "started_at"?: string | null;
  "completed_at"?: string | null;
  "updated_at": string;
  "workspace": string;
  "worktree": string;
  "arguments_sha256": string;
  "capability_fingerprint": string;
  "idempotency_key_sha256"?: string | null;
  "process_slot"?: boolean;
  "result"?: ToolResult | null;
}

export interface ToolInvocationListResponse {
  "run_id": string;
  "invocations": Array<ToolInvocationSummary>;
  "after_sequence"?: number;
  "next_sequence"?: number;
  "truncated"?: boolean;
}

export type ToolInvocationStatus = "requested" | "awaiting_approval" | "running" | "succeeded" | "failed" | "denied" | "cancelled" | "timed_out";

export interface ToolInvocationSummary {
  "invocation_sequence": number;
  "invocation_id": string;
  "invocation_revision": number;
  "run_id": string;
  "task_id": string;
  "tool_name": string;
  "tool_version": ToolVersion;
  "protocol_version": string;
  "caller_role": string;
  "status": string;
  "capabilities": Array<ToolCapability>;
  "policy_decision"?: ToolPolicyDecision | null;
  "approval_id"?: string | null;
  "resource_budget": ToolResourceBudget;
  "resource_usage": ToolResourceUsage;
  "cancellation": ToolCancellationSnapshot;
  "error_category"?: string | null;
  "error_message"?: string | null;
  "requested_at": string;
  "started_at"?: string | null;
  "completed_at"?: string | null;
  "updated_at": string;
}

export interface ToolLimitUsage {
  "requested"?: number | number | null;
  "supported": boolean;
  "enforced": boolean;
  "observed"?: number | number | null;
  "diagnostic"?: string | null;
}

export interface ToolListResponse {
  "tools": Array<ToolDescriptorSummary>;
  "total": number;
}

export interface ToolPolicyDecision {
  "outcome": ToolPolicyOutcome;
  "rule_id": string;
  "reason": string;
  "invocation_id": string;
  "invocation_revision": number;
  "capability_fingerprint": string;
  "arguments_sha256": string;
  "constraints"?: Array<ToolCapability>;
  "evaluated_at"?: string;
  "safe_metadata"?: Record<string, unknown>;
}

export interface ToolPolicyEvaluationRequest {
  "run_id": string;
  "task_id": string;
  "tool_name": string;
  "arguments"?: Record<string, unknown>;
  "expected_capabilities"?: Array<ToolCapabilityName>;
  "caller_role"?: "planner" | "coder" | "verifier" | "reviewer";
  "workspace_trusted"?: boolean;
  "provider_consented"?: boolean;
  "timeout_seconds"?: number | null;
  "resource_budget"?: ToolResourceBudget;
  "invocation_revision"?: number;
}

export interface ToolPolicyEvaluationResponse {
  "diagnostic_only"?: true;
  "persisted"?: false;
  "decision": ToolPolicyDecision;
  "required_capabilities": Array<ToolCapability>;
}

export type ToolPolicyOutcome = "allow" | "deny" | "require_approval" | "allow_with_constraints";

export interface ToolPolicyResponse {
  "policy_id"?: "agentbus-default-v1";
  "outcomes": Array<string>;
  "configuration": Record<string, unknown>;
  "rules": Array<Record<string, string>>;
}

export interface ToolResourceBudget {
  "wall_clock_seconds"?: number;
  "stdout_bytes"?: number;
  "stderr_bytes"?: number;
  "combined_output_bytes"?: number;
  "artifact_bytes"?: number;
  "child_processes"?: number;
  "concurrent_processes"?: number;
  "invocations_per_task"?: number;
  "invocations_per_run"?: number;
  "file_mutations"?: number;
  "total_written_bytes"?: number;
  "maximum_file_bytes"?: number;
  "memory_bytes"?: number | null;
  "cpu_seconds"?: number | null;
}

export interface ToolResourceUsage {
  "wall_clock_seconds"?: number;
  "stdout_bytes"?: number;
  "stderr_bytes"?: number;
  "artifact_bytes"?: number;
  "child_processes"?: number;
  "file_mutations"?: number;
  "written_bytes"?: number;
  "memory_bytes"?: number | null;
  "cpu_seconds"?: number | null;
  "limits"?: Record<string, ToolLimitUsage>;
}

export interface ToolResult {
  "invocation_id": string;
  "invocation_revision": number;
  "status": ToolInvocationStatus;
  "structured_output"?: Record<string, unknown>;
  "stdout"?: string;
  "stderr"?: string;
  "stdout_truncated"?: boolean;
  "stderr_truncated"?: boolean;
  "artifacts"?: Array<ToolArtifact>;
  "error"?: ToolError | null;
  "exit_code"?: number | null;
  "duration_seconds"?: number;
  "timed_out"?: boolean;
  "cancellation"?: ToolCancellationSnapshot;
  "resource_usage"?: ToolResourceUsage;
  "policy_decision": ToolPolicyDecision;
  "approval_id"?: string | null;
  "safe_diagnostic_metadata"?: Record<string, unknown>;
}

export interface ToolVersion {
  "major": number;
  "minor"?: number;
  "patch"?: number;
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
