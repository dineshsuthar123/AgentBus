import type {
    ApprovalDecisionResponse,
    ApprovalListResponse,
    AttemptListResponse,
  ChangeListResponse,
  DiffResponse,
  DoctorResponse,
  InfoResponse,
  ProviderListResponse,
  ProvenanceResponse,
  ReplayAcceptedResponse,
  ReplayCreateRequest,
  ReplayListResponse,
  ReplaySessionResponse,
  RunAcceptedResponse,
  RunCreateRequest,
  RunListResponse,
  RunReplayabilityResponse,
  RunReportResponse,
  RunSummary,
  SchedulerResponse,
  TaskListResponse,
  ToolAuditListResponse,
  ToolInvocationListResponse,
    TraceResponse,
    TraceSpanListResponse,
    TraceVerificationResponse,
  UsageResponse,
  WorkspaceValidationResponse,
  WorktreeListResponse
} from "./types";

export interface ControlErrorBody {
  error?: { code?: string; message?: string; retryable?: boolean };
}

export class StudioApiError extends Error {
  public constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
    public readonly retryable = false
  ) {
    super(message);
    this.name = "StudioApiError";
  }
}

export type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export class StudioClient {
  private readonly origin: URL;

  public constructor(
    private readonly token: string,
    origin = window.location.origin,
    private readonly fetcher: FetchLike = fetch
  ) {
    if (token.trim().length < 32) {
      throw new Error("The AgentBus session token must contain at least 32 characters.");
    }
    this.origin = validateStudioOrigin(origin);
  }

  public info() { return this.request<InfoResponse>("GET", "/api/v1/info"); }
  public providers() { return this.request<ProviderListResponse>("GET", "/api/v1/providers"); }
  public doctor(workspace?: string) {
    const query = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    return this.request<DoctorResponse>("GET", `/api/v1/doctor${query}`);
  }
  public runs(limit = 100) { return this.request<RunListResponse>("GET", `/api/v1/runs?limit=${limit}`); }
  public run(runId: string) { return this.request<RunSummary>("GET", `/api/v1/runs/${segment(runId)}`); }
  public tasks(runId: string) { return this.request<TaskListResponse>("GET", `/api/v1/runs/${segment(runId)}/tasks`); }
  public attempts(runId: string) { return this.request<AttemptListResponse>("GET", `/api/v1/runs/${segment(runId)}/attempts?limit=500`); }
  public approvals(runId: string) { return this.request<ApprovalListResponse>("GET", `/api/v1/runs/${segment(runId)}/approvals`); }
  public report(runId: string) { return this.request<RunReportResponse>("GET", `/api/v1/runs/${segment(runId)}/report`); }
  public changes(runId: string) { return this.request<ChangeListResponse>("GET", `/api/v1/runs/${segment(runId)}/changes`); }
  public diff(runId: string, path?: string) {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    return this.request<DiffResponse>("GET", `/api/v1/runs/${segment(runId)}/diff${query}`);
  }
  public invocations(runId: string) {
    return this.request<ToolInvocationListResponse>("GET", `/api/v1/runs/${segment(runId)}/tool-invocations?limit=500`);
  }
  public audit(runId: string) {
    return this.request<ToolAuditListResponse>("GET", `/api/v1/runs/${segment(runId)}/tool-audit?limit=500`);
  }
  public trace(runId: string) { return this.request<TraceResponse>("GET", `/api/v1/runs/${segment(runId)}/trace`); }
  public verifyTrace(runId: string) { return this.request<TraceVerificationResponse>("POST", `/api/v1/runs/${segment(runId)}/trace/verify`); }
  public traceSpans(runId: string) {
    return this.request<TraceSpanListResponse>("GET", `/api/v1/runs/${segment(runId)}/trace/spans?limit=500`);
  }
  public provenance(runId: string) { return this.request<ProvenanceResponse>("GET", `/api/v1/runs/${segment(runId)}/provenance`); }
  public replayability(runId: string) {
    return this.request<RunReplayabilityResponse>("GET", `/api/v1/runs/${segment(runId)}/replayability?limit=500`);
  }
  public replays(traceId?: string) {
    const query = new URLSearchParams({ limit: "500" });
    if (traceId) query.set("source_trace_id", traceId);
    return this.request<ReplayListResponse>("GET", `/api/v1/replays?${query.toString()}`);
  }
  public replay(replayId: string) { return this.request<ReplaySessionResponse>("GET", `/api/v1/replays/${segment(replayId)}`); }
  public scheduler(runId: string) { return this.request<SchedulerResponse>("GET", `/api/v1/runs/${segment(runId)}/scheduler`); }
  public usage(runId: string) { return this.request<UsageResponse>("GET", `/api/v1/runs/${segment(runId)}/usage`); }
  public worktrees(runId: string) { return this.request<WorktreeListResponse>("GET", `/api/v1/runs/${segment(runId)}/worktrees`); }

  public validateWorkspace(workspace: string) {
    return this.request<WorkspaceValidationResponse>("POST", "/api/v1/workspaces/validate", {
      workspace,
      require_git: true
    });
  }

  public createRun(body: RunCreateRequest) {
    return this.request<RunAcceptedResponse>("POST", "/api/v1/runs", body);
  }

  public decideApproval(runId: string, approvalId: string, revision: number, decision: "approve" | "reject", reason?: string) {
    return this.request<ApprovalDecisionResponse>(
      "POST",
      `/api/v1/runs/${segment(runId)}/approvals/${segment(approvalId)}/${decision}`,
      { revision, reason: reason?.trim() || undefined }
    );
  }

  public createReplay(runId: string, body: ReplayCreateRequest = { mode: "offline" }) {
    return this.request<ReplayAcceptedResponse>("POST", `/api/v1/runs/${segment(runId)}/replays`, body);
  }

  public resume(runId: string) {
    return this.request<{ run_id: string; status: string; resumed: boolean }>("POST", `/api/v1/runs/${segment(runId)}/resume`);
  }

  public cancel(runId: string, reason: string) {
    return this.request<{ run_id: string; status: string; cancellation_requested: boolean }>(
      "POST",
      `/api/v1/runs/${segment(runId)}/cancel`,
      { reason }
    );
  }

  public eventsUrl(runId?: string, cursor = 0): URL {
    const path = runId ? `/api/v1/runs/${segment(runId)}/events` : "/api/v1/events";
    const url = new URL(path, this.origin);
    if (cursor > 0) url.searchParams.set("after", String(cursor));
    return url;
  }

  public authorizationHeader(): string { return `Bearer ${this.token}`; }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 30_000);
    try {
      const response = await this.fetcher(new URL(path, this.origin), {
        method,
        headers: {
          Accept: "application/json",
          Authorization: this.authorizationHeader(),
          ...(body === undefined ? {} : { "Content-Type": "application/json" })
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal
      });
      if (!response.ok) {
        let payload: ControlErrorBody = {};
        try { payload = await response.json() as ControlErrorBody; } catch { /* bounded fallback */ }
        throw new StudioApiError(
          payload.error?.code ?? "http_error",
          payload.error?.message ?? `AgentBus request failed with HTTP ${response.status}.`,
          response.status,
          payload.error?.retryable ?? false
        );
      }
      return await response.json() as T;
    } finally {
      window.clearTimeout(timer);
    }
  }
}

export function validateStudioOrigin(value: string): URL {
  const url = new URL(value);
  const host = url.hostname.replace(/^\[|\]$/g, "");
  if (url.protocol !== "http:" || !["127.0.0.1", "::1"].includes(host)) {
    throw new Error("AgentBus Studio must run from a numeric loopback HTTP origin.");
  }
  return new URL(url.origin);
}

function segment(value: string): string {
  if (!value || value === "." || value === ".." || value.includes("\0")) {
    throw new Error("Unsafe AgentBus identifier.");
  }
  return encodeURIComponent(value);
}
