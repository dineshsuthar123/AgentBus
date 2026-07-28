import type {
  ApprovalDecisionRequest,
  ApprovalDecisionResponse,
  ApprovalListResponse,
  CancelResponse,
  ChangeListResponse,
  ComparisonCreateRequest,
  ComparisonResponse,
  DiffResponse,
  DoctorResponse,
  ErrorResponse,
  FileContentResponse,
  InfoResponse,
  McpServerCheckResponse,
  McpServerListResponse,
  ProviderListResponse,
  ReplayAcceptedResponse,
  ReplayCancelResponse,
  ReplayCreateRequest,
  ReplayListResponse,
  ReplaySessionResponse,
  ResumeResponse,
  RunReplayabilityResponse,
  RunAcceptedResponse,
  RunCreateRequest,
  RunListResponse,
  RunReportResponse,
  RunSummary,
  SchedulerResponse,
  TaskListResponse,
  TraceResponse,
  TraceSpanDetailResponse,
  TraceSpanListResponse,
  ToolAuditListResponse,
  ToolDescriptorDetail,
  ToolInvocationCancelResponse,
  ToolInvocationDetail,
  ToolInvocationListResponse,
  ToolListResponse,
  ToolPolicyResponse,
  UsageResponse,
  WorkspaceValidationRequest,
  WorkspaceValidationResponse,
  WorktreeListResponse
} from "./generated/protocol";
import { redactText } from "./redaction";

export type FetchLike = (
  input: string | URL,
  init?: RequestInit
) => Promise<Response>;

export class AgentBusApiError extends Error {
  public constructor(
    public readonly code: string,
    message: string,
    public readonly retryable: boolean,
    public readonly status: number
  ) {
    super(redactText(message));
    this.name = "AgentBusApiError";
  }
}

export class AgentBusClient {
  private readonly baseUrl: URL;

  public constructor(
    baseUrl: string,
    private readonly token: string,
    private readonly fetcher: FetchLike = fetch
  ) {
    this.baseUrl = validateLoopbackBaseUrl(baseUrl);
    if (token.length < 32) {
      throw new Error("AgentBus daemon token is invalid.");
    }
  }

  public info(): Promise<InfoResponse> {
    return this.request("GET", "/api/v1/info");
  }

  public validateWorkspace(
    body: WorkspaceValidationRequest
  ): Promise<WorkspaceValidationResponse> {
    return this.request("POST", "/api/v1/workspaces/validate", body);
  }

  public providers(): Promise<ProviderListResponse> {
    return this.request("GET", "/api/v1/providers");
  }

  public doctor(workspace?: string): Promise<DoctorResponse> {
    const query = workspace
      ? `?workspace=${encodeURIComponent(workspace)}`
      : "";
    return this.request("GET", `/api/v1/doctor${query}`);
  }

  public tools(): Promise<ToolListResponse> {
    return this.request("GET", "/api/v1/tools");
  }

  public tool(toolName: string): Promise<ToolDescriptorDetail> {
    return this.request("GET", `/api/v1/tools/${safeSegment(toolName)}`);
  }

  public toolPolicy(): Promise<ToolPolicyResponse> {
    return this.request("GET", "/api/v1/policy");
  }

  public mcpServers(): Promise<McpServerListResponse> {
    return this.request("GET", "/api/v1/mcp/servers");
  }

  public checkMcpServer(serverId: string): Promise<McpServerCheckResponse> {
    return this.request(
      "POST",
      `/api/v1/mcp/servers/${safeSegment(serverId)}/check`
    );
  }

  public createRun(body: RunCreateRequest): Promise<RunAcceptedResponse> {
    return this.request("POST", "/api/v1/runs", body);
  }

  public listRuns(limit = 100): Promise<RunListResponse> {
    return this.request("GET", `/api/v1/runs?limit=${limit}`);
  }

  public run(runId: string): Promise<RunSummary> {
    return this.request("GET", `/api/v1/runs/${safeSegment(runId)}`);
  }

  public resume(runId: string): Promise<ResumeResponse> {
    return this.request("POST", `/api/v1/runs/${safeSegment(runId)}/resume`);
  }

  public cancel(runId: string, reason?: string): Promise<CancelResponse> {
    return this.request(
      "POST",
      `/api/v1/runs/${safeSegment(runId)}/cancel`,
      reason ? { reason } : undefined
    );
  }

  public tasks(runId: string): Promise<TaskListResponse> {
    return this.request("GET", `/api/v1/runs/${safeSegment(runId)}/tasks`);
  }

  public trace(runId: string): Promise<TraceResponse> {
    return this.request("GET", `/api/v1/runs/${safeSegment(runId)}/trace`);
  }

  public traceSpans(
    runId: string,
    after = 0,
    limit = 500
  ): Promise<TraceSpanListResponse> {
    validatePage(after, limit);
    return this.request(
      "GET",
      `/api/v1/runs/${safeSegment(
        runId
      )}/trace/spans?after=${after}&limit=${limit}`
    );
  }

  public traceSpan(
    runId: string,
    spanId: string
  ): Promise<TraceSpanDetailResponse> {
    return this.request(
      "GET",
      `/api/v1/runs/${safeSegment(
        runId
      )}/trace/spans/${safeSegment(spanId)}`
    );
  }

  public replayability(
    runId: string,
    after = 0,
    limit = 500
  ): Promise<RunReplayabilityResponse> {
    validatePage(after, limit);
    return this.request(
      "GET",
      `/api/v1/runs/${safeSegment(
        runId
      )}/replayability?after=${after}&limit=${limit}`
    );
  }

  public createReplay(
    runId: string,
    body: ReplayCreateRequest
  ): Promise<ReplayAcceptedResponse> {
    return this.request(
      "POST",
      `/api/v1/runs/${safeSegment(runId)}/replays`,
      body
    );
  }

  public listReplays(
    sourceTraceId?: string,
    status?: string,
    limit = 500
  ): Promise<ReplayListResponse> {
    validatePage(0, limit);
    if (status && !REPLAY_STATUSES.has(status)) {
      throw new Error("AgentBus replay status filter is invalid.");
    }
    const query = new URLSearchParams({ limit: String(limit) });
    if (sourceTraceId) query.set("source_trace_id", sourceTraceId);
    if (status) query.set("status", status);
    return this.request("GET", `/api/v1/replays?${query.toString()}`);
  }

  public replay(replayId: string): Promise<ReplaySessionResponse> {
    return this.request(
      "GET",
      `/api/v1/replays/${safeSegment(replayId)}`
    );
  }

  public cancelReplay(replayId: string): Promise<ReplayCancelResponse> {
    return this.request(
      "POST",
      `/api/v1/replays/${safeSegment(replayId)}/cancel`
    );
  }

  public createComparison(
    body: ComparisonCreateRequest,
    after = 0,
    limit = 500
  ): Promise<ComparisonResponse> {
    validatePage(after, limit);
    return this.request(
      "POST",
      `/api/v1/comparisons?after=${after}&limit=${limit}`,
      body
    );
  }

  public comparison(
    comparisonId: string,
    after = 0,
    limit = 500
  ): Promise<ComparisonResponse> {
    validatePage(after, limit);
    return this.request(
      "GET",
      `/api/v1/comparisons/${safeSegment(
        comparisonId
      )}?after=${after}&limit=${limit}`
    );
  }

  public scheduler(runId: string): Promise<SchedulerResponse> {
    return this.request(
      "GET",
      `/api/v1/runs/${safeSegment(runId)}/scheduler`
    );
  }

  public worktrees(runId: string): Promise<WorktreeListResponse> {
    return this.request(
      "GET",
      `/api/v1/runs/${safeSegment(runId)}/worktrees`
    );
  }

  public usage(runId: string): Promise<UsageResponse> {
    return this.request("GET", `/api/v1/runs/${safeSegment(runId)}/usage`);
  }

  public toolInvocations(
    runId: string,
    after = 0,
    limit = 500
  ): Promise<ToolInvocationListResponse> {
    validatePage(after, limit);
    return this.request(
      "GET",
      `/api/v1/runs/${safeSegment(
        runId
      )}/tool-invocations?after=${after}&limit=${limit}`
    );
  }

  public toolInvocation(
    runId: string,
    invocationId: string
  ): Promise<ToolInvocationDetail> {
    return this.request(
      "GET",
      `/api/v1/runs/${safeSegment(
        runId
      )}/tool-invocations/${safeSegment(invocationId)}`
    );
  }

  public cancelToolInvocation(
    runId: string,
    invocationId: string,
    reason?: string
  ): Promise<ToolInvocationCancelResponse> {
    return this.request(
      "POST",
      `/api/v1/runs/${safeSegment(
        runId
      )}/tool-invocations/${safeSegment(invocationId)}/cancel`,
      reason ? { reason } : undefined
    );
  }

  public toolAudit(
    runId: string,
    after = 0,
    limit = 500
  ): Promise<ToolAuditListResponse> {
    validatePage(after, limit);
    return this.request(
      "GET",
      `/api/v1/runs/${safeSegment(
        runId
      )}/tool-audit?after=${after}&limit=${limit}`
    );
  }

  public report(runId: string): Promise<RunReportResponse> {
    return this.request("GET", `/api/v1/runs/${safeSegment(runId)}/report`);
  }

  public approvals(runId: string): Promise<ApprovalListResponse> {
    return this.request(
      "GET",
      `/api/v1/runs/${safeSegment(runId)}/approvals`
    );
  }

  public decideApproval(
    runId: string,
    approvalId: string,
    decision: "approve" | "reject",
    body: ApprovalDecisionRequest
  ): Promise<ApprovalDecisionResponse> {
    return this.request(
      "POST",
      `/api/v1/runs/${safeSegment(runId)}/approvals/${safeSegment(
        approvalId
      )}/${decision}`,
      body
    );
  }

  public changes(runId: string): Promise<ChangeListResponse> {
    return this.request("GET", `/api/v1/runs/${safeSegment(runId)}/changes`);
  }

  public diff(runId: string, path?: string): Promise<DiffResponse> {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    return this.request(
      "GET",
      `/api/v1/runs/${safeSegment(runId)}/diff${query}`
    );
  }

  public file(
    runId: string,
    path: string,
    revision: "before" | "after"
  ): Promise<FileContentResponse> {
    const safePath = path
      .split("/")
      .map((part) => safeSegment(part))
      .join("/");
    return this.request(
      "GET",
      `/api/v1/runs/${safeSegment(runId)}/changes/${safePath}?revision=${revision}`
    );
  }

  public eventsUrl(runId?: string): URL {
    return new URL(
      runId
        ? `/api/v1/runs/${safeSegment(runId)}/events`
        : "/api/v1/events",
      this.baseUrl
    );
  }

  public authorizationHeader(): string {
    return `Bearer ${this.token}`;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<T> {
    const url = new URL(path, this.baseUrl);
    const response = await this.fetcher(url, {
      method,
      headers: {
        Accept: "application/json",
        Authorization: this.authorizationHeader(),
        ...(body === undefined ? {} : { "Content-Type": "application/json" })
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30_000)
    });
    if (!response.ok) {
      throw await mapError(response);
    }
    return (await response.json()) as T;
  }
}

export function validateLoopbackBaseUrl(value: string): URL {
  const url = new URL(value);
  const hostname = url.hostname.replace(/^\[|\]$/g, "");
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "::1"].includes(hostname) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new Error("AgentBus daemon URL must be an uncredentialed loopback HTTP URL.");
  }
  return new URL(url.origin);
}

async function mapError(response: Response): Promise<AgentBusApiError> {
  let body: Partial<ErrorResponse> = {};
  try {
    body = (await response.json()) as Partial<ErrorResponse>;
  } catch {
    // The stable fallback intentionally ignores untrusted raw response text.
  }
  const error = body.error;
  return new AgentBusApiError(
    error?.code ?? "http_error",
    error?.message ?? `AgentBus request failed with HTTP ${response.status}.`,
    error?.retryable ?? false,
    response.status
  );
}

function safeSegment(value: string): string {
  if (!value || value === "." || value === ".." || value.includes("\0")) {
    throw new Error("Unsafe AgentBus protocol path segment.");
  }
  return encodeURIComponent(value);
}

function validatePage(after: number, limit: number): void {
  if (
    !Number.isSafeInteger(after) ||
    after < 0 ||
    !Number.isSafeInteger(limit) ||
    limit < 1 ||
    limit > 500
  ) {
    throw new Error("AgentBus pagination is outside the bounded range.");
  }
}

const REPLAY_STATUSES = new Set([
  "pending",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "incompatible",
  "awaiting_input"
]);
