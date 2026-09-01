import { Activity, CircleStop, FileCode2, Fingerprint, LoaderCircle, PanelRightOpen, RefreshCw, RotateCcw, ShieldCheck } from "lucide-react";
import { lazy, Suspense, useEffect, useEffectEvent, useMemo, useState } from "react";
import type { ApprovalSummary, TraceVerificationResponse } from "../api/types";
import { loadRunBundle, type RunBundle } from "../api/runBundle";
import { PanelBoundary } from "../components/PanelBoundary";
import { ErrorPanel, LoadingState, StatusSignal, TraceIdentity } from "../components/Primitives";
import { displayWorkspace, durationBetween, humanize } from "../lib/format";
import { ContextInspector } from "../observatory/ContextInspector";
import { ExecutionMesh } from "../observatory/ExecutionMesh";
import { FlightRecorder, type TimelinePresentation } from "../observatory/FlightRecorder";
import { IntegritySpine, type PresentationMode } from "../observatory/IntegritySpine";
import { buildExecutionMesh, isPendingApproval, projectExecutionMesh, type MeshEdge, type MeshNode } from "../observatory/model";
import type { InspectorView, ObservatorySelection } from "../observatory/selection";
import { buildTimeline, type TimelineRecord } from "../observatory/timelineModel";
import { useLayoutPreferences } from "../state/layoutPreferences";
import { useRunEvents, useStudio } from "../state/StudioContext";

const SourceLens = lazy(() => import("../observatory/SourceLens").then((module) => ({ default: module.SourceLens })));

export function RunPage({ runId }: { runId: string }) {
  const { client, streamStatus } = useStudio();
  const runEvents = useRunEvents(runId);
  const [bundle, setBundle] = useState<RunBundle>();
  const [selection, setSelection] = useState<ObservatorySelection>({ kind: "view", view: "run" });
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState<string>();
  const [refreshing, setRefreshing] = useState(false);
  const [traceVerification, setTraceVerification] = useState<TraceVerificationResponse>();
  const [focusRequest, setFocusRequest] = useState(0);
  const [announcement, setAnnouncement] = useState("");
  const [presentation, setPresentation] = useState<TimelinePresentation>({ mode: "live" });
  const [sourceOpen, setSourceOpen] = useState(false);
  const [preferences, setPreferences] = useLayoutPreferences();
  const eventVersion = runEvents.version;

  useEffect(() => {
    if (!client) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setRefreshing(true);
      void loadRunBundle(client, runId, { force: eventVersion > 0, signal: controller.signal })
        .then((next) => {
          if (!controller.signal.aborted) {
            setBundle(next);
            setError(undefined);
          }
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Run state could not be loaded.");
        })
        .finally(() => {
          if (!controller.signal.aborted) setRefreshing(false);
        });
    }, eventVersion > 0 ? 120 : 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [client, eventVersion, runId]);

  const model = useMemo(() => bundle ? buildExecutionMesh(bundle) : undefined, [bundle]);
  const timeline = useMemo(() => bundle ? buildTimeline(bundle, runEvents.events) : [], [bundle, runEvents.events]);
  const presentationRecord = presentation.recordId ? timeline.find((record) => record.id === presentation.recordId) : undefined;
  const presentedModel = useMemo(
    () => model && presentation.mode !== "live" ? projectExecutionMesh(model, presentationRecord?.timestamp) : model,
    [model, presentation.mode, presentationRecord?.timestamp]
  );
  const pendingApproval = bundle?.approvals.approvals.find(isPendingApproval);
  const pendingNode = pendingApproval && model?.nodes.find((node) => node.approvalId === pendingApproval.approval_id);

  async function refresh() {
    if (!client) return;
    setRefreshing(true);
    try {
      setBundle(await loadRunBundle(client, runId, { force: true }));
      setError(undefined);
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(reason instanceof Error ? reason.message : "Run state could not be refreshed.");
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
      setAnnouncement(decision === "approve" ? "Approval confirmed by AgentBus. Execution may continue." : "Rejection confirmed by AgentBus. The run remains stopped.");
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "The approval decision was not accepted.");
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
    const reason = window.prompt("Why should this durable run be cancelled?", "Cancelled from AgentBus Studio");
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
      for (let attempt = 0; attempt < 12; attempt += 1) {
        const session = await client.replay(accepted.replay_id, { force: true });
        if (isTerminal(session.status)) break;
        await new Promise((resolve) => window.setTimeout(resolve, 250));
      }
      await refresh();
      setAnnouncement("Offline replay completed. Captured and dispatched work remain explicitly distinguished.");
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

  const handleCommand = useEffectEvent((command: string) => {
    if (!bundle || !model) return;
    if (command === "focus-active") {
      const active = model.nodes.find((node) => node.id === model.activeNodeId);
      if (active) selectNode(active);
      setFocusRequest((value) => value + 1);
    } else if (command === "approval") {
      if (pendingNode) selectNode(pendingNode);
    } else if (command === "source") selectView("source");
    else if (command === "verifier") selectView("verifier");
    else if (command === "verify-trace") void verifyEvidence();
    else if (command === "replay") void replay();
    else if (command === "copy-run") void copyIdentity(bundle.run.run_id, "Run ID copied");
    else if (command === "copy-trace" && bundle.trace) void copyIdentity(bundle.trace.trace_id, "Trace ID copied");
  });

  useEffect(() => {
    const onCommand = (event: Event) => handleCommand(String((event as CustomEvent).detail ?? ""));
    window.addEventListener("agentbus:studio-command", onCommand);
    return () => window.removeEventListener("agentbus:studio-command", onCommand);
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
  const mode: PresentationMode = presentation.mode === "replay" ? "REPLAY" : presentation.mode === "manual" || terminal ? "HISTORICAL" : "LIVE";
  const effectiveSelection: ObservatorySelection = pendingNode ? { kind: "node", node: pendingNode } : selection;
  const inspectorCollapsed = preferences.inspectorCollapsed && !pendingNode;

  function selectNode(node: MeshNode) {
    setSelection({ kind: "node", node });
    setPreferences((current) => ({ ...current, inspectorCollapsed: false }));
    updateDeepLink(runId, { node: node.id, attempt: node.attemptNumber ? String(node.attemptNumber) : undefined });
  }

  function selectEdge(edge: MeshEdge) {
    setSelection({ kind: "edge", edge });
    setPreferences((current) => ({ ...current, inspectorCollapsed: false }));
  }

  function selectView(view: InspectorView) {
    if (view === "source") setSourceOpen(true);
    setSelection({ kind: "view", view });
    setPreferences((current) => ({ ...current, inspectorCollapsed: false }));
    updateDeepLink(runId, { view });
  }

  async function copyIdentity(value: string, message: string) {
    await navigator.clipboard?.writeText(value);
    setAnnouncement(message);
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
          <button type="button" onClick={() => selectView("review")}><ShieldCheck size={14} /> Review</button>
          <button type="button" onClick={() => selectView("evidence")}><Fingerprint size={14} /> Evidence</button>
          <button type="button" onClick={() => void refresh()} disabled={refreshing || busy !== undefined} aria-label="Refresh run">{refreshing ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}</button>
          {!terminal && <button type="button" onClick={() => void cancel()} disabled={busy !== undefined} aria-label="Cancel run"><CircleStop size={14} /></button>}
          {isResumable(bundle) && <button type="button" onClick={() => void resume()} disabled={busy !== undefined}>{busy === "resume" ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />} Resume</button>}
        </div>
      </header>

      {error && <div className="run-error-strip"><ErrorPanel message={error} /></div>}
      {bundle.partialErrors && <div className="partial-state" role="status"><Activity size={13} /><span>Partial snapshot</span><strong>{Object.keys(bundle.partialErrors).join(", ")} unavailable</strong></div>}

      <div className="observatory-grid">
        <PanelBoundary name="Execution mesh"><div className={`execution-source-stage ${sourceOpen ? "source-is-open" : ""}`}><ExecutionMesh model={presentedModel!} selectedId={effectiveSelection.kind === "node" ? effectiveSelection.node.id : undefined} highlightTaskId={highlightedTaskId} onSelectNode={selectNode} onSelectEdge={selectEdge} focusRequest={focusRequest} />{sourceOpen && <Suspense fallback={<LoadingState label="Opening source lens" />}><SourceLens bundle={bundle} client={client} timeline={timeline} highlightTaskId={highlightedTaskId} initialPath={effectiveSelection.kind === "file" ? effectiveSelection.path : undefined} onSelectFile={selectFile} onLocateTask={locateTask} onClose={() => setSourceOpen(false)} /></Suspense>}</div></PanelBoundary>
        {!inspectorCollapsed ? <PanelBoundary name={pendingApproval ? "Approval inspector" : "Context inspector"} safetyCritical={Boolean(pendingApproval)}><ContextInspector bundle={bundle} selection={effectiveSelection} busy={busy} traceVerification={traceVerification} onDecision={decide} onReplay={replay} onVerify={verifyEvidence} onClose={pendingApproval ? undefined : () => setPreferences((current) => ({ ...current, inspectorCollapsed: true }))} /></PanelBoundary> : <button className="inspector-reopen" type="button" onClick={() => setPreferences((current) => ({ ...current, inspectorCollapsed: false }))}><PanelRightOpen size={15} /><span>Inspector</span></button>}
        <IntegritySpine bundle={bundle} mode={mode} stream={streamStatus} onSelect={selectView} />
      </div>

      <FlightRecorder events={timeline} presentation={presentation} terminal={terminal} collapsed={preferences.timelineCollapsed} onChange={setPresentation} onSelect={selectTimelineRecord} onToggle={() => setPreferences((current) => ({ ...current, timelineCollapsed: !current.timelineCollapsed }))} />
      <div className="sr-only" aria-live="polite" aria-atomic="true">{pendingApproval ? "Approval required. Execution is paused at the exact policy gate." : announcement}</div>
    </div>
  );
}

function isTerminal(status: string): boolean {
  return ["succeeded", "completed", "failed", "rejected", "cancelled"].includes(status.toLowerCase());
}

function isResumable(bundle: RunBundle): boolean {
  return ["failed", "cancelled", "paused", "waiting_for_approval"].includes(bundle.run.status.toLowerCase()) && !bundle.approvals.approvals.some(isPendingApproval);
}

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || target instanceof HTMLElement && target.isContentEditable;
}

function updateDeepLink(runId: string, values: Record<string, string | undefined>) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value) query.set(key, value);
  const suffix = query.size ? `?${query.toString()}` : "";
  window.history.replaceState(null, "", `#/runs/${encodeURIComponent(runId)}${suffix}`);
}
