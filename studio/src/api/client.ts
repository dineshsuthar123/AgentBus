import type {
    ApprovalDecisionResponse,
    ApprovalListResponse,
    AttemptListResponse,
  ChangeListResponse,
  DiffResponse,
  DoctorResponse,
  FileContentResponse,
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

export interface ReadRequestOptions {
  force?: boolean;
  signal?: AbortSignal;
  staleTimeMilliseconds?: number;
  timeoutMilliseconds?: number;
}

interface CachedRead {
  storedAt: number;
  value: unknown;
}

interface InflightRead {
  consumers: Set<symbol>;
  controller: AbortController;
  promise: Promise<unknown>;
  settled: boolean;
}

export class StudioClient {
  private readonly origin: URL;
  private readonly fetcher: FetchLike;
  private readonly cache = new Map<string, CachedRead>();
  private readonly inflight = new Map<string, InflightRead>();
  private cacheHits = 0;
  private deduplicatedReads = 0;

  public constructor(
    private readonly token: string,
    origin = window.location.origin,
    fetcher?: FetchLike
  ) {
    if (token.trim().length < 32) {
      throw new Error("The AgentBus session token must contain at least 32 characters.");
    }
    this.origin = validateStudioOrigin(origin);
    this.fetcher = fetcher ?? bindBrowserFetch();
  }

  public info(options?: ReadRequestOptions) { return this.read<InfoResponse>("/api/v1/info", options); }
  public providers(options?: ReadRequestOptions) { return this.read<ProviderListResponse>("/api/v1/providers", options); }
  public doctor(workspace?: string, options?: ReadRequestOptions) {
    const query = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    return this.read<DoctorResponse>(`/api/v1/doctor${query}`, options);
  }
  public runs(limit = 100, options?: ReadRequestOptions) { return this.read<RunListResponse>(`/api/v1/runs?limit=${limit}`, options); }
  public run(runId: string, options?: ReadRequestOptions) { return this.read<RunSummary>(`/api/v1/runs/${segment(runId)}`, options); }
  public tasks(runId: string, options?: ReadRequestOptions) { return this.read<TaskListResponse>(`/api/v1/runs/${segment(runId)}/tasks`, options); }
  public attempts(runId: string, options?: ReadRequestOptions) { return this.read<AttemptListResponse>(`/api/v1/runs/${segment(runId)}/attempts?limit=500`, options); }
  public approvals(runId: string, options?: ReadRequestOptions) { return this.read<ApprovalListResponse>(`/api/v1/runs/${segment(runId)}/approvals`, options); }
  public report(runId: string, options?: ReadRequestOptions) { return this.read<RunReportResponse>(`/api/v1/runs/${segment(runId)}/report`, options); }
  public changes(runId: string, options?: ReadRequestOptions) { return this.read<ChangeListResponse>(`/api/v1/runs/${segment(runId)}/changes`, options); }
  public diff(runId: string, path?: string, options?: ReadRequestOptions) {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    return this.read<DiffResponse>(`/api/v1/runs/${segment(runId)}/diff${query}`, options);
  }
  public file(runId: string, path: string, revision: "before" | "after", options?: ReadRequestOptions) {
    const safePath = path.split(/[\\/]/).filter(Boolean).map(segment).join("/");
    if (!safePath) throw new Error("Unsafe AgentBus path.");
    return this.read<FileContentResponse>(
      `/api/v1/runs/${segment(runId)}/changes/${safePath}?revision=${revision}`,
      options
    );
  }
  public invocations(runId: string, options?: ReadRequestOptions) {
    return this.read<ToolInvocationListResponse>(`/api/v1/runs/${segment(runId)}/tool-invocations?limit=500`, options);
  }
  public audit(runId: string, options?: ReadRequestOptions) {
    return this.read<ToolAuditListResponse>(`/api/v1/runs/${segment(runId)}/tool-audit?limit=500`, options);
  }
  public trace(runId: string, options?: ReadRequestOptions) { return this.read<TraceResponse>(`/api/v1/runs/${segment(runId)}/trace`, options); }
  public verifyTrace(runId: string) { return this.request<TraceVerificationResponse>("POST", `/api/v1/runs/${segment(runId)}/trace/verify`); }
  public traceSpans(runId: string, options?: ReadRequestOptions) {
    return this.read<TraceSpanListResponse>(`/api/v1/runs/${segment(runId)}/trace/spans?limit=500`, options);
  }
  public provenance(runId: string, options?: ReadRequestOptions) { return this.read<ProvenanceResponse>(`/api/v1/runs/${segment(runId)}/provenance`, options); }
  public replayability(runId: string, options?: ReadRequestOptions) {
    return this.read<RunReplayabilityResponse>(`/api/v1/runs/${segment(runId)}/replayability?limit=500`, options);
  }
  public replays(traceId?: string, options?: ReadRequestOptions) {
    const query = new URLSearchParams({ limit: "500" });
    if (traceId) query.set("source_trace_id", traceId);
    return this.read<ReplayListResponse>(`/api/v1/replays?${query.toString()}`, options);
  }
  public replay(replayId: string, options?: ReadRequestOptions) { return this.read<ReplaySessionResponse>(`/api/v1/replays/${segment(replayId)}`, options); }
  public scheduler(runId: string, options?: ReadRequestOptions) { return this.read<SchedulerResponse>(`/api/v1/runs/${segment(runId)}/scheduler`, options); }
  public usage(runId: string, options?: ReadRequestOptions) { return this.read<UsageResponse>(`/api/v1/runs/${segment(runId)}/usage`, options); }
  public worktrees(runId: string, options?: ReadRequestOptions) { return this.read<WorktreeListResponse>(`/api/v1/runs/${segment(runId)}/worktrees`, options); }

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

  public queryCacheSummary(): {
    cachedReads: number;
    cacheHits: number;
    deduplicatedReads: number;
    inflightReads: number;
  } {
    return {
      cachedReads: this.cache.size,
      cacheHits: this.cacheHits,
      deduplicatedReads: this.deduplicatedReads,
      inflightReads: this.inflight.size
    };
  }

  public invalidate(pathPrefix = "/api/v1/"): void {
    for (const key of this.cache.keys()) {
      if (key.startsWith(pathPrefix)) this.cache.delete(key);
    }
  }

  private read<T>(path: string, options: ReadRequestOptions = {}): Promise<T> {
    const staleTime = Math.max(0, options.staleTimeMilliseconds ?? 500);
    const cached = this.cache.get(path);
    if (!options.force && cached && Date.now() - cached.storedAt <= staleTime) {
      this.cacheHits += 1;
      return abortedOrValue(cached.value as T, options.signal);
    }

    let entry = this.inflight.get(path);
    if (entry?.controller.signal.aborted) {
      this.inflight.delete(path);
      entry = undefined;
    }
    if (!entry) {
      const controller = new AbortController();
      const created: InflightRead = {
        consumers: new Set<symbol>(),
        controller,
        promise: Promise.resolve(undefined),
        settled: false
      };
      created.promise = this.readFromNetwork<T>(path, controller.signal, options.timeoutMilliseconds)
        .then((value) => {
          this.cache.set(path, { storedAt: Date.now(), value });
          return value;
        })
        .finally(() => {
          created.settled = true;
          if (this.inflight.get(path) === created) this.inflight.delete(path);
        });
      entry = created;
      this.inflight.set(path, created);
    } else {
      this.deduplicatedReads += 1;
    }
    return this.consumeInflight<T>(entry, options.signal);
  }

  private consumeInflight<T>(entry: InflightRead, signal?: AbortSignal): Promise<T> {
    if (signal?.aborted) return Promise.reject(abortError());
    const consumer = Symbol("studio-read");
    entry.consumers.add(consumer);
    return new Promise<T>((resolve, reject) => {
      const onAbort = () => {
        release();
        reject(abortError());
      };
      const release = () => {
        signal?.removeEventListener("abort", onAbort);
        entry.consumers.delete(consumer);
        if (!entry.settled && entry.consumers.size === 0) entry.controller.abort();
      };
      signal?.addEventListener("abort", onAbort, { once: true });
      entry.promise.then(
        (value) => {
          if (signal?.aborted) return;
          release();
          resolve(value as T);
        },
        (error: unknown) => {
          if (signal?.aborted) return;
          release();
          reject(error);
        }
      );
    });
  }

  private async readFromNetwork<T>(path: string, signal: AbortSignal, timeoutMilliseconds = 30_000): Promise<T> {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        return await this.fetchJson<T>("GET", path, undefined, signal, timeoutMilliseconds);
      } catch (error) {
        if (attempt > 0 || signal.aborted || !isRetryableReadError(error)) throw error;
        await delay(120, signal);
      }
    }
    throw new Error("Unreachable read retry state.");
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    return this.fetchJson<T>(method, path, body, undefined, 30_000);
  }

  private async fetchJson<T>(
    method: string,
    path: string,
    body: unknown,
    externalSignal: AbortSignal | undefined,
    timeoutMilliseconds: number
  ): Promise<T> {
    const controller = new AbortController();
    const abort = () => controller.abort();
    externalSignal?.addEventListener("abort", abort, { once: true });
    const timer = window.setTimeout(abort, Math.max(1, timeoutMilliseconds));
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
          payload.error?.retryable ?? response.status >= 500
        );
      }
      return await response.json() as T;
    } finally {
      window.clearTimeout(timer);
      externalSignal?.removeEventListener("abort", abort);
    }
  }
}

export function bindBrowserFetch(): FetchLike {
  return window.fetch.bind(window);
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

function abortedOrValue<T>(value: T, signal?: AbortSignal): Promise<T> {
  return signal?.aborted ? Promise.reject(abortError()) : Promise.resolve(value);
}

function abortError(): DOMException {
  return new DOMException("AgentBus request was cancelled.", "AbortError");
}

function isRetryableReadError(error: unknown): boolean {
  return error instanceof StudioApiError ? error.retryable : error instanceof TypeError;
}

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const timer = window.setTimeout(done, milliseconds);
    signal.addEventListener("abort", cancelled, { once: true });
    function done() {
      signal.removeEventListener("abort", cancelled);
      resolve();
    }
    function cancelled() {
      window.clearTimeout(timer);
      reject(abortError());
    }
  });
}
