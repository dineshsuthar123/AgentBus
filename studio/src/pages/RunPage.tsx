import { Activity, CircleStop, FileCode2, LoaderCircle, RefreshCw, RotateCcw, ScrollText, ShieldCheck } from "lucide-react";
import { lazy, Suspense, useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import type { ApprovalSummary, ReplaySessionResponse, TraceVerificationResponse } from "../api/types";
import { ReplaySession } from "../components/ReplaySession";
import { loadRunBundle, type RunBundle } from "../api/runBundle";
import { StudioApiError } from "../api/client";
import { PanelBoundary } from "../components/PanelBoundary";
import { ApprovalGate } from "../components/ApprovalGate";
import { ExecutionOverview } from "../components/ExecutionOverview";
import { ErrorPanel, LoadingState, StatusSignal, TraceIdentity } from "../components/Primitives";
import { displayWorkspace, durationBetween, humanize } from "../lib/format";
import { ContextInspector } from "../observatory/ContextInspector";
import { ExecutionMesh } from "../observatory/ExecutionMesh";
import { FlightRecorder, type TimelinePresentation } from "../observatory/FlightRecorder";
import { buildExecutionMesh, isPendingApproval, projectExecutionMesh, type MeshEdge, type MeshNode } from "../observatory/model";
import type { InspectorView, ObservatorySelection } from "../observatory/selection";
import { buildTimeline, type TimelineRecord } from "../observatory/timelineModel";
import { useLayoutPreferences } from "../state/layoutPreferences";
import { useRunEvents, useStudio } from "../state/StudioContext";

const SourceLens = lazy(() => import("../observatory/SourceLens").then((module) => ({ default: module.SourceLens })));
const EvidenceWorkbench = lazy(() => import("../components/EvidenceWorkbench").then((module) => ({ default: module.EvidenceWorkbench })));
const EMPTY_QUERY = new URLSearchParams();

export function RunPage({ query = EMPTY_QUERY, runId }: { query?: URLSearchParams; runId: string }) {
  const { client } = useStudio();
  const runEvents = useRunEvents(runId);
  const [bundle, setBundle] = useState<RunBundle>();
  const [selection, setSelection] = useState<ObservatorySelection>({ kind: "view", view: "run" });
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState<string>();
  const [refreshing, setRefreshing] = useState(false);
  const [traceVerification, setTraceVerification] = useState<TraceVerificationResponse>();
  const [focusRequest, setFocusRequest] = useState(0);
  const [announcement, setAnnouncement] = useState("");
  const [replaySession, setReplaySession] = useState<ReplaySessionResponse>();
  const [presentation, setPresentation] = useState<TimelinePresentation>({ mode: "live" });
  const [sourceOpen, setSourceOpen] = useState(false);
  const [preferences, setPreferences] = useLayoutPreferences();
  const deepLinkApplied = useRef<string | undefined>(undefined);
  const scheduleReconciliation = useRef<(() => void) | undefined>(undefined);
  const eventVersion = runEvents.version;
  const selectionQuery = ["view", "file", "node", "attempt"].map((key) => `${key}=${query.get(key) ?? ""}`).join("&");

  const applyDeepLink = useEffectEvent((next: RunBundle) => {
    if (deepLinkApplied.current === selectionQuery) return;
    const linked = selectionFromDeepLink(query, next) ?? { selection: { kind: "view", view: "run" } as ObservatorySelection, sourceOpen: false };
    setSelection(linked.selection);
    setSourceOpen(linked.sourceOpen);
    setPreferences((current) => ({ ...current, inspectorCollapsed: false }));
    deepLinkApplied.current = selectionQuery;
  });
  const receiveBundle = useEffectEvent((next: RunBundle) => {
    setBundle(next);
    setError(undefined);
    applyDeepLink(next);
  });
  const applyCurrentDeepLink = useEffectEvent(() => { if (bundle) applyDeepLink(bundle); });
  useEffect(() => { applyCurrentDeepLink(); }, [selectionQuery]);

  useEffect(() => {
    if (!client) return;
    const controller = new AbortController();
    let timer: number | undefined;
    let inFlight = false;
    let dirty = false;
    let first = true;
    function schedule() {
      dirty = true;
      if (inFlight || timer !== undefined || controller.signal.aborted) return;
      timer = window.setTimeout(() => { void reconcile(); }, first ? 0 : 120);
    }
    async function reconcile() {
      timer = undefined;
      dirty = false;
      inFlight = true;
      setRefreshing(true);
      try {
        const next = await loadRunBundle(client!, runId, { force: !first, signal: controller.signal });
        if (!controller.signal.aborted) receiveBundle(next);
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Run state could not be loaded.");
      } finally {
        inFlight = false;
        first = false;
        if (!controller.signal.aborted) {
          setRefreshing(false);
          if (dirty) schedule();
        }
      }
    }
    scheduleReconciliation.current = schedule;
    schedule();
    return () => {
      window.clearTimeout(timer);
      controller.abort();
      scheduleReconciliation.current = undefined;
    };
  }, [client, runId]);

  useEffect(() => { if (eventVersion > 0) scheduleReconciliation.current?.(); }, [eventVersion]);

  const model = useMemo(() => bundle ? buildExecutionMesh(bundle) : undefined, [bundle]);
  const timeline = useMemo(() => bundle ? buildTimeline(bundle, runEvents.events) : [], [bundle, runEvents.events]);
  const presentationRecord = presentation.recordId ? timeline.find((record) => record.id === presentation.recordId) : undefined;
  const presentedModel = useMemo(
    () => model && presentation.mode !== "live" ? projectExecutionMesh(model, presentationRecord?.timestamp) : model,
    [model, presentation.mode, presentationRecord?.timestamp]
  );
  const pendingApprovals = bundle?.approvals.approvals.filter(isPendingApproval) ?? [];
  const pendingApproval = pendingApprovals.find((approval) => approval.approval_id === query.get("approval")) ?? pendingApprovals[0];
  const pendingNode = pendingApproval && model?.nodes.find((node) => node.approvalId === pendingApproval.approval_id);

  async function refresh() {
    if (!client) return false;
    setRefreshing(true);
    try {
      setBundle(await loadRunBundle(client, runId, { force: true }));
      setError(undefined);
      return true;
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(reason instanceof Error ? reason.message : "Run state could not be refreshed.");
      return false;
    } finally {
      setRefreshing(false);
    }
  }

  async function decide(approval: ApprovalSummary, decision: "approve" | "reject", reason?: string) {
    if (!client) return;
    setBusy(`approval:${approval.approval_id}`);
    setError(undefined);
    try {
      await client.decideApproval(runId, approval.approval_id, approval.revision ?? 1, decision, reason);
      if (decision === "approve") await client.resume(runId);
      await refresh();
      setAnnouncement(decision === "approve" ? "Approval confirmed by Syndra. Execution may continue." : "Rejection confirmed by Syndra. The run remains stopped.");
    } catch (reasonValue) {
      if (reasonValue instanceof StudioApiError && reasonValue.status === 409) {
        const current = await refresh();
        setError(`This request changed or was resolved in another client. Your decision was not applied. ${current ? "Inspect the refreshed request before deciding." : "The snapshot is stale; refresh again before deciding."}`);
      } else setError(reasonValue instanceof Error ? reasonValue.message : "The approval decision was not accepted.");
    } finally {
      setBusy(undefined);
    }
  }

  async function resume() {
    if (!client) return;
    setBusy("resume");
    try { await client.resume(runId); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Run resume failed."); }
    finally { setBusy(undefined); }
  }

  async function cancel() {
    if (!client) return;
    const reason = window.prompt("Why should this durable run be cancelled?", "Cancelled from Syndra Studio");
    if (reason === null || !reason.trim()) return;
    setBusy("cancel");
    try { await client.cancel(runId, reason.trim()); await refresh(); }
    catch (reasonValue) { setError(reasonValue instanceof Error ? reasonValue.message : "Cancellation request failed."); }
    finally { setBusy(undefined); }
  }

  async function replay() {
    if (!client || !bundle?.replayability?.replayable_offline) return;
    setBusy("replay");
    setSelection({ kind: "view", view: "evidence" });
    try {
      const accepted = await client.createReplay(runId, { mode: "offline" });
      setReplaySession(accepted);
      setAnnouncement(`Offline replay accepted: ${accepted.replay_id}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Offline replay could not be started.");
    } finally {
      setBusy(undefined);
    }
  }

  async function verifyEvidence() {
    if (!client) return;
    setBusy("verify-trace");
    setSelection({ kind: "view", view: "evidence" });
    try { setTraceVerification(await client.verifyTrace(runId)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Trace integrity verification failed."); }
    finally { setBusy(undefined); }
  }

  function selectNode(node: MeshNode) {
    setSelection({ kind: "node", node });
    setPreferences((current) => ({ ...current, inspectorCollapsed: false }));
    updateDeepLink(runId, { node: node.id, attempt: node.attemptNumber ? String(node.attemptNumber) : undefined });
  }

  function selectView(view: InspectorView) {
    setSourceOpen(view === "source");
    setSelection({ kind: "view", view });
    setPreferences((current) => ({ ...current, inspectorCollapsed: false }));
    updateDeepLink(runId, { view });
  }

  async function copyIdentity(value: string, message: string) {
    await navigator.clipboard?.writeText(value);
    setAnnouncement(message);
  }

  const handleCommand = useEffectEvent((command: string) => {
    if (!bundle || !model) return;
    if (command === "focus-active") {
      const active = model.nodes.find((node) => node.id === model.activeNodeId);
      if (active) selectNode(active);
      setFocusRequest((value) => value + 1);
    } else if (command === "approval") {
      if (pendingNode) selectNode(pendingNode);
      if (pendingApproval) {
        const heading = document.getElementById(`approval-title-${pendingApproval.approval_id}`);
        heading?.scrollIntoView({ block: "nearest" });
        heading?.focus({ preventScroll: true });
      }
    } else if (command === "source") selectView("source");
    else if (command === "verifier") selectView("verifier");
    else if (command === "verify-trace") void verifyEvidence();
    else if (command === "replay") void replay();
    else if (command === "copy-run") void copyIdentity(bundle.run.run_id, "Run ID copied");
    else if (command === "copy-trace" && bundle.trace) void copyIdentity(bundle.trace.trace_id, "Trace ID copied");
  });

  useEffect(() => {
    const onCommand = (event: Event) => handleCommand(String((event as CustomEvent).detail ?? ""));
    window.addEventListener("syndra:studio-command", onCommand);
    return () => window.removeEventListener("syndra:studio-command", onCommand);
  }, []);

  const handleShortcut = useEffectEvent((event: KeyboardEvent) => {
    if (isEditableTarget(event.target) || event.ctrlKey || event.metaKey || event.altKey || !bundle || !model) return;
    const key = event.key.toLowerCase();
    if (key === "f") handleCommand("focus-active");
    else if (key === "d") selectView("source");
    else if (key === "e") selectView("evidence");
    else if ((event.key === "[" || event.key === "]") && model) {
      const attempts = model.nodes.filter((node) => node.kind === "coder" && node.attemptNumber);
      if (!attempts.length) return;
      const current = selection.kind === "node" ? attempts.findIndex((node) => node.id === selection.node.id) : -1;
      const direction = event.key === "]" ? 1 : -1;
      selectNode(attempts[(Math.max(0, current) + direction + attempts.length) % attempts.length]);
    }
    else if (key === "escape" && selection.kind !== "view") selectView("run");
    else return;
    event.preventDefault();
  });

  useEffect(() => {
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);

  if (!bundle && !error) return <div className="observatory-loading"><LoadingState label="Loading durable run topology" /></div>;
  if (!bundle) return <div className="page"><ErrorPanel message={error ?? "Run not found."} action={<a className="text-link" href="#/history">Return to history</a>} /></div>;

  const terminal = isTerminal(bundle.run.status);
  const effectiveSelection = selection;
  const inspectorCollapsed = preferences.inspectorCollapsed && !pendingNode;

  function selectEdge(edge: MeshEdge) {
    setSelection({ kind: "edge", edge });
    setPreferences((current) => ({ ...current, inspectorCollapsed: false }));
  }

  function selectTimelineRecord(record: TimelineRecord) {
    if (record.event) setSelection({ kind: "event", event: record.event });
    else if (record.nodeId) {
      const node = model?.nodes.find((item) => item.id === record.nodeId);
      if (node) setSelection({ kind: "node", node });
    }
    setPreferences((current) => ({ ...current, inspectorCollapsed: false }));
  }

  function selectFile(path: string) {
    setSelection({ kind: "file", path });
    setPreferences((current) => ({ ...current, inspectorCollapsed: false }));
    updateDeepLink(runId, { view: "source", file: path });
  }

  function locateTask(taskId: string) {
    const node = [...(model?.nodes ?? [])].reverse().find((item) => item.taskId === taskId && item.kind === "coder")
      ?? model?.nodes.find((item) => item.taskId === taskId);
    if (node) selectNode(node);
  }

  const highlightedTaskId = effectiveSelection.kind === "node" ? effectiveSelection.node.taskId
    : effectiveSelection.kind === "file" ? bundle.changes?.changes.find((change) => change.path === effectiveSelection.path)?.task_id ?? undefined
      : undefined;

  return (
    <div className={`run-observatory density-${preferences.density} presentation-${presentation.mode} ${inspectorCollapsed ? "inspector-collapsed" : ""} ${preferences.timelineCollapsed ? "timeline-collapsed" : ""}`}>
      <header className="run-command-strip">
        <div className="run-command-title"><StatusSignal status={bundle.run.status} /><h1>{bundle.run.original_task}</h1><span>{humanize(bundle.run.workflow)}</span></div>
        <div className="run-command-meta"><TraceIdentity label="Run" value={bundle.run.run_id} compact /><span><small>Repository</small><strong>{displayWorkspace(bundle.run.workspace)}</strong></span><span><small>Elapsed</small><strong>{durationBetween(bundle.run.created_at, bundle.run.completed_at)}</strong></span></div>
        <div className="run-command-actions">
          <button type="button" onClick={() => selectView("source")}><FileCode2 size={14} /> Source</button>
          <button type="button" onClick={() => selectView("runtime")}><ScrollText size={14} /> Events</button>
          <button type="button" onClick={() => selectView("review")}><ShieldCheck size={14} /> Review</button>
          <button type="button" onClick={() => void refresh()} disabled={refreshing || busy !== undefined} aria-label="Refresh run">{refreshing ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}</button>
          {!terminal && <button type="button" onClick={() => void cancel()} disabled={busy !== undefined} aria-label="Cancel run"><CircleStop size={14} /></button>}
          {isResumable(bundle) && <button type="button" onClick={() => void resume()} disabled={busy !== undefined}>{busy === "resume" ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />} Resume</button>}
        </div>
      </header>

      {error && <div className="run-error-strip"><ErrorPanel message={error} /></div>}
      {bundle.partialErrors && <div className="partial-state" role="status"><Activity size={13} /><span>Partial snapshot</span><strong>{Object.keys(bundle.partialErrors).join(", ")} unavailable</strong></div>}
      <nav className="run-sections" aria-label="Run sections">
        {([["run", "Task"], ["planner", "Plan"], ["source", "Changes"], ["verifier", "Checks"], ["evidence", "Evidence"]] as const).map(([view, label]) => <button key={view} aria-pressed={selection.kind === "view" && selection.view === view || view === "source" && sourceOpen} onClick={() => selectView(view)}>{label}</button>)}
      </nav>
      {pendingApproval && <a className="approval-jump" href={`#approval-title-${pendingApproval.approval_id}`} onClick={(event) => { event.preventDefault(); const heading = document.getElementById(`approval-title-${pendingApproval.approval_id}`); heading?.scrollIntoView({ block: "nearest" }); heading?.focus({ preventScroll: true }); }}>Approval needed: inspect this exact request</a>}
      {pendingApprovals.length > 1 && <label className="approval-selector">Pending permission requests ({pendingApprovals.length})<select value={pendingApproval!.approval_id} onChange={(event) => { const parameters = new URLSearchParams(query); parameters.set("approval", event.target.value); window.location.hash = `#/runs/${encodeURIComponent(runId)}?${parameters}`; }}>{pendingApprovals.slice(0, 100).map((approval) => <option key={approval.approval_id} value={approval.approval_id}>{approval.task_id}: {approval.requested_action} / revision {approval.revision}</option>)}</select>{pendingApprovals.length > 100 && <span>Showing the first 100 requests. Resolve these to reveal remaining work.</span>}</label>}
      <div className={`artifact-spread ${pendingApproval ? "has-decision" : ""}`}>
        <div className="folio-run-artifact">
        {!sourceOpen && selection.kind === "view" && selection.view === "run" && <ExecutionOverview bundle={bundle} onView={selectView} onTask={locateTask} />}
        {sourceOpen && <Suspense fallback={<LoadingState label="Opening changes" />}><SourceLens bundle={bundle} client={client} timeline={timeline} highlightTaskId={highlightedTaskId} initialPath={effectiveSelection.kind === "file" ? effectiveSelection.path : undefined} onSelectFile={selectFile} onLocateTask={locateTask} onClose={() => setSourceOpen(false)} /></Suspense>}
        <PanelBoundary name="Run artifact"><ContextInspector bundle={bundle} events={runEvents.events} selection={pendingApproval ? selection.kind === "node" && selection.node.kind === "approval" ? { kind: "view", view: "run" } : selection : effectiveSelection} busy={busy} traceVerification={traceVerification} onDecision={decide} onReplay={replay} onVerify={verifyEvidence} /></PanelBoundary>
        </div>
        {pendingApproval && <PanelBoundary name="Exact approval" safetyCritical><aside className="context-inspector decision-attachment" aria-label="Selected exact request"><div className="inspector-scroll"><ApprovalGate approval={pendingApproval} busy={busy === `approval:${pendingApproval.approval_id}`} onDecision={(decision, reason) => decide(pendingApproval, decision, reason)} /></div></aside></PanelBoundary>}
      </div>
      {bundle.approvals.approvals.some((approval) => !isPendingApproval(approval)) && <details className="resolved-permissions"><summary>Resolved permission records</summary><p>A decision authorizes or rejects a request; it does not prove execution or verification succeeded.</p><ul>{bundle.approvals.approvals.filter((approval) => !isPendingApproval(approval)).slice(0, 100).map((approval) => <li key={approval.approval_id}><strong>{approval.requested_action}</strong><StatusSignal status={approval.state} /><code>{approval.approval_id}</code><span>Revision {approval.revision}</span></li>)}</ul></details>}
      {client && selection.kind === "view" && selection.view === "evidence" && <Suspense fallback={<LoadingState label="Opening evidence management" />}><EvidenceWorkbench key={runId} bundle={bundle} client={client} /></Suspense>}
      <details className="folio-dependencies"><summary>Dependencies and execution graph</summary><ExecutionMesh model={presentedModel!} selectedId={effectiveSelection.kind === "node" ? effectiveSelection.node.id : undefined} highlightTaskId={highlightedTaskId} onSelectNode={selectNode} onSelectEdge={selectEdge} focusRequest={focusRequest} /></details>
      <details className="folio-activity"><summary>Activity and captured timeline</summary><FlightRecorder events={timeline} presentation={presentation} terminal={terminal} collapsed={preferences.timelineCollapsed} onChange={setPresentation} onSelect={selectTimelineRecord} onToggle={() => setPreferences((current) => ({ ...current, timelineCollapsed: !current.timelineCollapsed }))} /></details>
      {replaySession && client && <ReplaySession key={replaySession.replay_id} client={client} initial={replaySession} />}
      <div className="sr-only" aria-live="polite" aria-atomic="true">{pendingApproval ? "Approval required. Execution is paused at the exact policy gate." : announcement}</div>
    </div>
  );
}

function isTerminal(status: string): boolean {
  return ["succeeded", "completed", "failed", "rejected", "cancelled"].includes(status.toLowerCase());
}

function isResumable(bundle: RunBundle): boolean {
  return bundle.run.workflow !== "single" && ["failed", "cancelled", "paused", "waiting_for_approval"].includes(bundle.run.status.toLowerCase()) && !bundle.approvals.approvals.some(isPendingApproval);
}

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || target instanceof HTMLElement && target.isContentEditable;
}

function updateDeepLink(runId: string, values: Record<string, string | undefined>) {
  const query = new URLSearchParams(window.location.hash.split("?")[1]);
  for (const key of ["view", "file", "node", "attempt"]) query.delete(key);
  for (const [key, value] of Object.entries(values)) if (value) query.set(key, value);
  const suffix = query.size ? `?${query.toString()}` : "";
  window.location.hash = `#/runs/${encodeURIComponent(runId)}${suffix}`;
}

function selectionFromDeepLink(query: URLSearchParams, bundle: RunBundle): { selection: ObservatorySelection; sourceOpen: boolean } | undefined {
  const file = query.get("file");
  if (file && bundle.changes?.changes.some((change) => change.path === file)) {
    return { selection: { kind: "file", path: file }, sourceOpen: true };
  }

  const model = buildExecutionMesh(bundle);
  const nodeId = query.get("node");
  const node = nodeId ? model.nodes.find((item) => item.id === nodeId) : undefined;
  if (node) return { selection: { kind: "node", node }, sourceOpen: false };

  const attemptValue = query.get("attempt");
  if (attemptValue) {
    const attempt = model.nodes.find((item) => item.attemptId === attemptValue || String(item.attemptNumber ?? "") === attemptValue);
    if (attempt) return { selection: { kind: "node", node: attempt }, sourceOpen: false };
  }

  const view = query.get("view");
  if (view && isInspectorView(view)) return { selection: { kind: "view", view }, sourceOpen: view === "source" };
  return undefined;
}

function isInspectorView(value: string): value is InspectorView {
  return ["run", "planner", "source", "verifier", "review", "evidence", "runtime"].includes(value);
}
