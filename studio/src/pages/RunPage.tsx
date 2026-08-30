import {
  Activity,
  Ban,
  Boxes,
  CheckCheck,
  CircleStop,
  FileCode2,
  Fingerprint,
  GitCompareArrows,
  History,
  LoaderCircle,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  TerminalSquare,
  Wrench
} from "lucide-react";
import { useEffect, useState } from "react";
import type { ApprovalSummary, ReplaySessionResponse, RunSummary, TaskSummary, TraceVerificationResponse } from "../api/types";
import { loadRunBundle, type RunBundle } from "../api/runBundle";
import { ApprovalGate } from "../components/ApprovalGate";
import { AttemptStack } from "../components/AttemptStack";
import { DiffViewer } from "../components/DiffViewer";
import { EvidenceRibbon } from "../components/EvidenceRibbon";
import { ExecutionRail } from "../components/ExecutionRail";
import { ErrorPanel, LoadingState, StatusSignal, TraceIdentity, VerificationMark } from "../components/Primitives";
import { SourcePulse } from "../components/SourcePulse";
import { ToolTimeline } from "../components/ToolTimeline";
import { VerificationSeal } from "../components/VerificationSeal";
import { arrayOfStrings, durationBetween, formatDate, humanize, recordOf, shortId } from "../lib/format";
import { useStudio } from "../state/StudioContext";

type RunTab = "overview" | "tools" | "attempts" | "source" | "review" | "evidence";

const tabs: Array<{ id: RunTab; label: string; icon: React.ReactNode }> = [
  { id: "overview", label: "Overview", icon: <Activity size={15} /> },
  { id: "tools", label: "Tools", icon: <TerminalSquare size={15} /> },
  { id: "attempts", label: "Attempts", icon: <History size={15} /> },
  { id: "source", label: "Source", icon: <FileCode2 size={15} /> },
  { id: "review", label: "Review", icon: <CheckCheck size={15} /> },
  { id: "evidence", label: "Evidence", icon: <Fingerprint size={15} /> }
];

export function RunPage({ runId }: { runId: string }) {
  const { client, eventRevision, streamConnected } = useStudio();
  const [bundle, setBundle] = useState<RunBundle>();
  const [tab, setTab] = useState<RunTab>("overview");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState<string>();
  const [traceVerification, setTraceVerification] = useState<TraceVerificationResponse>();

  async function refresh() {
    if (!client) return;
    try {
      const next = await loadRunBundle(client, runId);
      setBundle(next);
      setError(undefined);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Run state could not be loaded.");
    }
  }

  useEffect(() => {
    let active = true;
    if (!client) return;
    void loadRunBundle(client, runId).then((next) => {
      if (active) { setBundle(next); setError(undefined); }
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "Run state could not be loaded.");
    });
    return () => { active = false; };
  }, [client, runId, eventRevision]);

  if (!bundle && !error) return <div className="page"><LoadingState label="Loading durable run state" /></div>;
  if (!bundle) return <div className="page"><ErrorPanel message={error ?? "Run not found."} action={<a className="text-link" href="#/history">Return to history</a>} /></div>;

  const pendingApprovals = bundle.approvals.approvals.filter(isPendingApproval);
  const latestReplay = bundle.replays?.replays[0];
  const terminal = isTerminal(bundle.run.status);

  async function decide(approval: ApprovalSummary, decision: "approve" | "reject", reason?: string) {
    if (!client) return;
    setBusy(`approval:${approval.approval_id}`);
    try {
      await client.decideApproval(runId, approval.approval_id, approval.revision ?? 1, decision, reason);
      await refresh();
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
    if (!client) return;
    setBusy("replay");
    try {
      const accepted = await client.createReplay(runId, { mode: "offline" });
      for (let attempt = 0; attempt < 12; attempt += 1) {
        const session = await client.replay(accepted.replay_id);
        if (isTerminal(session.status)) break;
        await new Promise((resolve) => window.setTimeout(resolve, 250));
      }
      await refresh();
      setTab("evidence");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Offline replay could not be started.");
    } finally {
      setBusy(undefined);
    }
  }

  async function verifyEvidence() {
    if (!client) return;
    setBusy("verify-trace");
    try {
      setTraceVerification(await client.verifyTrace(runId));
      setTab("evidence");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Trace integrity verification failed.");
    } finally {
      setBusy(undefined);
    }
  }

  return (
    <div className="run-workspace">
      <EvidenceRibbon trace={bundle.trace} provenance={bundle.provenance} replayability={bundle.replayability} replay={latestReplay} streamConnected={streamConnected} />
      <div className="page run-page">
        <RunHero run={bundle.run} tasks={bundle.tasks.tasks} />
        <div className="run-actions">
          <button className="button button-secondary" type="button" onClick={() => void refresh()} disabled={busy !== undefined}><RefreshCw size={14} /> Refresh</button>
          {!terminal && <button className="button button-danger-quiet" type="button" onClick={() => void cancel()} disabled={busy !== undefined}><CircleStop size={14} /> Cancel</button>}
          {isResumable(bundle.run) && <button className="button button-secondary" type="button" onClick={() => void resume()} disabled={busy !== undefined}>{busy === "resume" ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />} Resume</button>}
        </div>
        {error && <ErrorPanel message={error} />}

        <nav className="run-tabs" aria-label="Run details">
          {tabs.map((item) => <button className={tab === item.id ? "is-active" : ""} type="button" key={item.id} onClick={() => setTab(item.id)}>{item.icon}<span>{item.label}</span>{tabCount(item.id, bundle, pendingApprovals)}</button>)}
        </nav>

        <section className="run-tab-panel">
          {tab === "overview" && <Overview bundle={bundle} pendingApprovals={pendingApprovals} busy={busy} onDecision={decide} />}
          {tab === "tools" && <Tools bundle={bundle} />}
          {tab === "attempts" && <Attempts bundle={bundle} />}
          {tab === "source" && <Source bundle={bundle} />}
          {tab === "review" && <Review bundle={bundle} />}
          {tab === "evidence" && <Evidence bundle={bundle} latestReplay={latestReplay} verification={traceVerification} busy={busy} onReplay={replay} onVerify={verifyEvidence} />}
        </section>
      </div>
    </div>
  );
}

function RunHero({ run, tasks }: { run: RunSummary; tasks: TaskSummary[] }) {
  const completed = tasks.filter((task) => task.status === "succeeded").length;
  const progress = tasks.length ? Math.round((completed / tasks.length) * 100) : 0;
  return (
    <header className="run-hero">
      <div className="run-hero-copy">
        <div className="run-kicker"><StatusSignal status={run.status} /><span>{humanize(run.workflow)} workflow</span><span>v{run.version}</span></div>
        <h1>{run.original_task}</h1>
        <div className="run-identities"><TraceIdentity label="Run" value={run.run_id} /><TraceIdentity label="Workspace" value={run.workspace} /></div>
      </div>
      <div className="progress-dial" style={{ "--progress": `${progress * 3.6}deg` } as React.CSSProperties}>
        <div><strong>{completed}<small>/{tasks.length || "—"}</small></strong><span>tasks sealed</span></div>
      </div>
      <dl className="run-hero-stats">
        <div><dt>Started</dt><dd>{formatDate(run.created_at)}</dd></div>
        <div><dt>Elapsed</dt><dd>{durationBetween(run.created_at, run.completed_at)}</dd></div>
        <div><dt>Changed</dt><dd>{run.changed_files?.length ?? 0} files</dd></div>
      </dl>
    </header>
  );
}

function Overview({ bundle, pendingApprovals, busy, onDecision }: { bundle: RunBundle; pendingApprovals: ApprovalSummary[]; busy?: string; onDecision: (approval: ApprovalSummary, decision: "approve" | "reject", reason?: string) => Promise<void> }) {
  return (
    <div className="tab-stack">
      {pendingApprovals.map((approval) => <ApprovalGate key={approval.approval_id} approval={approval} busy={busy === `approval:${approval.approval_id}`} onDecision={(decision, reason) => onDecision(approval, decision, reason)} />)}
      <section className="content-section">
        <div className="section-heading"><div><p className="section-label">Durable topology</p><h2>Execution rail</h2></div><span className="technical-note">Immutable task attempts</span></div>
        <ExecutionRail run={bundle.run} tasks={bundle.tasks.tasks} spans={bundle.spans?.spans} waitingApproval={pendingApprovals.length > 0} />
      </section>
      <section className="content-section">
        <div className="section-heading"><div><p className="section-label">Task graph</p><h2>Done criteria by node</h2></div><span>{bundle.tasks.tasks.length} persisted</span></div>
        <div className="task-grid">{bundle.tasks.tasks.map((task) => <TaskNode task={task} key={task.task_id} />)}</div>
      </section>
      {bundle.run.failure_reason && <ErrorPanel title="Run reached a failed terminal state" message={bundle.run.failure_reason} />}
    </div>
  );
}

function TaskNode({ task }: { task: TaskSummary }) {
  return <article className="task-node"><div className="task-node-head"><span>{String(task.position + 1).padStart(2, "0")}</span><StatusSignal status={task.status} /></div><h3>{task.title}</h3><p>{task.description}</p><div className="criteria-list">{(task.done_criteria ?? []).map((criterion) => <span key={criterion}><CheckCheck size={13} />{criterion}</span>)}</div><footer><code>{task.assigned_role}</code><span>{task.attempts} attempt{task.attempts === 1 ? "" : "s"}</span></footer></article>;
}

function Tools({ bundle }: { bundle: RunBundle }) {
  return (
    <div className="tab-stack">
      <section className="content-section"><div className="section-heading"><div><p className="section-label">Managed execution</p><h2>Tool invocation timeline</h2></div><span>{bundle.invocations?.invocations.length ?? 0} invocations</span></div><ToolTimeline invocations={bundle.invocations?.invocations ?? []} /></section>
      <section className="content-section"><div className="section-heading"><div><p className="section-label">Policy ledger</p><h2>Audit decisions</h2></div><ShieldCheck size={18} /></div><div className="audit-ledger">{bundle.audit?.records.map((entry) => <div className="audit-row" key={entry.audit_sequence}><span>{entry.audit_sequence}</span><code>{entry.record.policy_decision.rule_id}</code><strong>{entry.record.tool_name}</strong><StatusSignal status={entry.record.outcome} /></div>)}{!bundle.audit?.records.length && <p className="quiet-copy">No tool audit records have been persisted.</p>}</div></section>
    </div>
  );
}

function Attempts({ bundle }: { bundle: RunBundle }) {
  return <section className="content-section"><div className="section-heading"><div><p className="section-label">Durable history</p><h2>Attempt stack</h2></div><span>Successful terminals never rerun on resume</span></div><AttemptStack tasks={bundle.tasks.tasks} attempts={bundle.attempts?.attempts} report={bundle.report?.report} /></section>;
}

function Source({ bundle }: { bundle: RunBundle }) {
  return (
    <div className="source-layout">
      <section className="content-section source-list"><div className="section-heading"><div><p className="section-label">Repository scope</p><h2>Observed files</h2></div><span>{bundle.changes?.changes.length ?? 0}</span></div><SourcePulse changes={bundle.changes?.changes ?? []} /></section>
      <section className="content-section diff-section"><div className="section-heading"><div><p className="section-label">Bounded patch</p><h2>Candidate diff</h2></div>{bundle.diff?.truncated && <span className="warning-chip">Truncated at {bundle.diff.byte_limit} bytes</span>}</div><DiffViewer diff={bundle.diff?.diff ?? ""} truncated={bundle.diff?.truncated} /></section>
    </div>
  );
}

function Review({ bundle }: { bundle: RunBundle }) {
  const report = bundle.report?.report ?? {};
  const reviewer = recordOf(report.reviewer);
  const reviewerSummary = stringFrom(report.reviewer_summary, reviewer.summary, report.review_summary);
  const issues = arrayOfStrings(report.reviewer_issues ?? reviewer.issues);
  const fixes = arrayOfStrings(report.required_fixes ?? reviewer.required_fixes);
  const verifierSummary = stringFrom(report.verifier_summary, recordOf(report.verifier).summary);
  return (
    <div className="review-layout">
      <section className="content-section"><div className="section-heading"><div><p className="section-label">Mechanical proof</p><h2>Verifier</h2></div><StatusSignal status={bundle.run.verifier_status} /></div><VerificationSeal status={bundle.run.verifier_status} summary={verifierSummary} /></section>
      <section className="content-section reviewer-decision"><div className="section-heading"><div><p className="section-label">Mandatory gate</p><h2>Final reviewer</h2></div><StatusSignal status={bundle.run.reviewer_status} /></div><div className="review-verdict"><VerificationMark valid={["approved", "passed", "succeeded"].includes(String(bundle.run.reviewer_status).toLowerCase())} /><div><strong>{reviewerSummary ?? (bundle.run.reviewer_status ? humanize(bundle.run.reviewer_status) : "Awaiting final review")}</strong><p>Final review remains required before AgentBus can commit or create a pull request.</p></div></div><ReviewList title="Issues" items={issues} /><ReviewList title="Required fixes" items={fixes} /></section>
    </div>
  );
}

function Evidence({ bundle, latestReplay, verification, busy, onReplay, onVerify }: { bundle: RunBundle; latestReplay?: ReplaySessionResponse; verification?: TraceVerificationResponse; busy?: string; onReplay: () => Promise<void>; onVerify: () => Promise<void> }) {
  const trace = bundle.trace;
  const provenance = bundle.provenance;
  return (
    <div className="evidence-layout">
      <section className="content-section evidence-manifest"><div className="section-heading"><div><p className="section-label">Flight recorder</p><h2>Trace manifest</h2></div><StatusSignal status={trace?.status} /></div><dl className="manifest-grid"><Manifest label="Trace ID" value={trace?.trace_id} /><Manifest label="Root span" value={trace?.root_span_id} /><Manifest label="Spans" value={trace?.span_count} /><Manifest label="Events" value={trace?.event_count} /><Manifest label="Checkpoints" value={trace?.checkpoint_count} /><Manifest label="Schema" value={trace?.schema_version} /></dl></section>
      <section className="content-section provenance-section"><div className="section-heading"><div><p className="section-label">Tamper evidence</p><h2>Provenance seal</h2></div><Fingerprint size={18} /></div>{provenance ? <><div className="integrity-root"><span>Integrity root</span><code>{provenance.integrity_root}</code></div><dl className="technical-grid"><div><dt>Algorithm</dt><dd>{provenance.integrity_algorithm}</dd></div><div><dt>Objects</dt><dd>{provenance.integrity_object_count}</dd></div><div><dt>Policy hash</dt><dd><code>{shortId(provenance.policy_sha256)}</code></dd></div><div><dt>Repository tree</dt><dd><code>{shortId(provenance.final_repository_tree_sha256)}</code></dd></div></dl>{verification && <div className="integrity-verdict"><VerificationMark valid={verification.valid ?? false} /><span><strong>{verification.valid ? "Integrity verified" : "Integrity mismatch"}</strong><small>{verification.object_count} objects checked, {verification.provider_calls ?? 0} provider calls, {verification.network_calls ?? 0} network calls</small></span></div>}<button className="button button-secondary" type="button" disabled={busy !== undefined} onClick={() => void onVerify()}>{busy === "verify-trace" ? <LoaderCircle className="spin" size={14} /> : <ShieldCheck size={14} />} Verify sealed trace</button></> : <p className="quiet-copy">Provenance is sealed when a trace reaches the required state.</p>}</section>
      <section className="content-section replay-section"><div className="section-heading"><div><p className="section-label">Providerless proof</p><h2>Offline replay</h2></div><GitCompareArrows size={18} /></div><p className="section-copy">Re-execute captured deterministic spans inside a daemon-managed temporary workspace. Offline mode does not call a model provider or the network.</p>{bundle.replayability && <div className="replayability"><span><strong>{humanize(bundle.replayability.level)}</strong><small>Replayability classification</small></span><StatusSignal status={bundle.replayability.replayable_offline ? "ready" : "incompatible"} /></div>}{latestReplay && <div className="replay-result"><div><StatusSignal status={latestReplay.status} /><TraceIdentity label="Replay" value={latestReplay.replay_id} /></div><dl><div><dt>Provider calls</dt><dd>{latestReplay.provider_calls ?? 0}</dd></div><div><dt>Network calls</dt><dd>{latestReplay.network_calls ?? 0}</dd></div><div><dt>Process dispatches</dt><dd>{latestReplay.process_dispatches ?? 0}</dd></div><div><dt>Captured results</dt><dd>{latestReplay.captured_tool_results_reused ?? 0}</dd></div></dl></div>}<button className="button button-replay" type="button" disabled={!bundle.replayability?.replayable_offline || busy !== undefined} onClick={() => void onReplay()}>{busy === "replay" ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />} Run offline replay</button></section>
      <section className="content-section span-section"><div className="section-heading"><div><p className="section-label">Trace topology</p><h2>Spans</h2></div><Boxes size={18} /></div><div className="span-list">{bundle.spans?.spans.map((span) => <div className="span-row" key={span.span_id}><span>{span.sequence}</span><Wrench size={14} /><strong>{span.name}</strong><code>{span.span_type}</code><StatusSignal status={span.status} /></div>)}{!bundle.spans?.spans.length && <p className="quiet-copy">No trace spans are available yet.</p>}</div></section>
    </div>
  );
}

function ReviewList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return <div className="review-list"><p className="section-label">{title}</p>{items.map((item) => <p key={item}><Ban size={13} />{item}</p>)}</div>;
}

function Manifest({ label, value }: { label: string; value: unknown }) {
  return <div><dt>{label}</dt><dd>{typeof value === "string" ? <code>{shortId(value)}</code> : String(value ?? "—")}</dd></div>;
}

function tabCount(tab: RunTab, bundle: RunBundle, approvals: ApprovalSummary[]) {
  const count = tab === "tools" ? bundle.invocations?.invocations.length : tab === "attempts" ? bundle.tasks.tasks.reduce((sum, task) => sum + task.attempts, 0) : tab === "source" ? bundle.changes?.changes.length : tab === "overview" ? approvals.length : undefined;
  return count ? <small>{count}</small> : null;
}

function isPendingApproval(approval: ApprovalSummary): boolean {
  return !["approved", "rejected", "expired", "cancelled"].includes(approval.state.toLowerCase());
}

function isTerminal(status: string): boolean {
  return ["succeeded", "completed", "failed", "rejected", "cancelled", "incompatible"].includes(status.toLowerCase());
}

function isResumable(run: RunSummary): boolean {
  return ["failed", "cancelled", "interrupted"].includes(run.status.toLowerCase()) && run.cancellation?.resume_eligible !== false;
}

function stringFrom(...values: unknown[]): string | undefined {
  return values.find((value): value is string => typeof value === "string" && value.trim().length > 0);
}
