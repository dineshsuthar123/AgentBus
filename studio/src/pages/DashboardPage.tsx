import { AlertTriangle, ArrowUpRight, CheckCircle2, CircleDot, FileCode2, Plus, RadioTower, Search, ShieldCheck } from "lucide-react";
import { useDeferredValue, useState } from "react";
import type { RunSummary } from "../api/types";
import { EmptyState, StatusSignal, TraceIdentity } from "../components/Primitives";
import { displayWorkspace, formatDate, humanize } from "../lib/format";
import { useStudio } from "../state/StudioContext";

export function DashboardPage() {
  const { runs, providers, doctor, info, streamStatus } = useStudio();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.toLowerCase());
  const active = runs.filter((run) => !isTerminal(run.status));
  const failed = runs.filter((run) => ["failed", "rejected"].includes(run.status.toLowerCase()));
  const successful = runs.filter((run) => ["succeeded", "completed"].includes(run.status.toLowerCase()));
  const changed = runs.reduce((total, run) => total + (run.changed_files?.length ?? 0), 0);
  const readyProviders = providers.filter((provider) => provider.ready);
  const recent = runs.filter((run) => `${run.run_id} ${run.original_task} ${run.workspace}`.toLowerCase().includes(deferredQuery));

  return (
    <div className="command-deck">
      <header className="deck-header">
        <div><span className="observatory-label">Operational command deck</span><h1>Execution, attention, integrity.</h1></div>
        <div className="deck-counters" role="region" aria-label="System signals">
          <span><CircleDot size={12} /><strong>{active.length}</strong> Active runs</span>
          <span><AlertTriangle size={12} /><strong>{failed.length}</strong> Needs attention</span>
          <span><CheckCircle2 size={12} /><strong>{successful.length}</strong> Completed</span>
          <span><FileCode2 size={12} /><strong>{changed}</strong> Observed files</span>
        </div>
        <a className="button button-primary" href="#/new"><Plus size={14} /> Launch run</a>
      </header>

      <div className="deck-primary">
        <section className="active-lane">
          <DeckHeading label="Active" title="Execution in motion" meta={`${active.length} durable`} />
          {active.length ? active.slice(0, 4).map((run) => <ActiveRun key={run.run_id} run={run} />) : <EmptyState title="Execution lane clear" action={<a className="text-link" href="#/new">Configure an execution <ArrowUpRight size={13} /></a>}>No running or policy-paused run is persisted.</EmptyState>}
        </section>

        <section className="attention-lane">
          <DeckHeading label="Attention" title="Human and failure gates" meta={streamStatus.connected ? "Stream healthy" : "Runtime degraded"} />
          {!streamStatus.connected && <AttentionItem icon={<RadioTower size={14} />} title="Event stream degraded" copy={`Last state remains visible at cursor ${streamStatus.cursor || 0}.`} tone="amber" href="#/runtime" />}
          {failed.slice(0, 3).map((run) => <AttentionItem key={run.run_id} icon={<AlertTriangle size={14} />} title={run.original_task} copy={run.failure_reason ?? `Run ${run.status}. Inspect verifier and review evidence.`} tone="coral" href={`#/runs/${encodeURIComponent(run.run_id)}`} />)}
          {!failed.length && streamStatus.connected && <AttentionItem icon={<ShieldCheck size={14} />} title="No unresolved terminal failures" copy="Approval gates and failures will appear here without hiding the execution lane." tone="emerald" />}
        </section>
      </div>

      <section className="system-topology">
        <DeckHeading label="System topology" title="Local control path" meta={String(info?.protocol_version ?? "Protocol unavailable")} />
        <div className="topology-line" aria-label="System topology">
          <TopologyNode label="Studio" detail="same origin" status="ready" />
          <TopologyEdge state={streamStatus.connected ? "active" : "degraded"} label="SSE" />
          <TopologyNode label="Daemon" detail={doctor?.status ?? "unknown"} status={doctor?.status ?? "unknown"} />
          <TopologyEdge state={readyProviders.length ? "active" : "muted"} label="routes" />
          <TopologyNode label="Providers" detail={`${readyProviders.length}/${providers.length} ready`} status={readyProviders.length ? "ready" : "offline"} />
          <TopologyEdge state="active" label="policy" />
          <TopologyNode label="Evidence" detail="trace + replay" status="ready" />
        </div>
      </section>

      <section className="recent-meshes">
        <div className="recent-toolbar"><DeckHeading label="Recent runs" title="Durable flight records" meta={`${recent.length}/${runs.length}`} /><label><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search task, repository, or run ID" /></label></div>
        <div className="mini-run-grid">{recent.slice(0, 8).map((run) => <MiniRunGraph key={run.run_id} run={run} />)}{!recent.length && <p className="quiet-copy">No persisted run matches this search.</p>}</div>
      </section>
    </div>
  );
}

function DeckHeading({ label, title, meta }: { label: string; title: string; meta: string }) {
  return <header className="deck-section-heading"><div><span>{label}</span><h2>{title}</h2></div><code>{meta}</code></header>;
}

function ActiveRun({ run }: { run: RunSummary }) {
  return <a className="active-run-card" href={`#/runs/${encodeURIComponent(run.run_id)}`}>
    <div className="active-run-index"><i /><span>{humanize(run.status)}</span></div>
    <div><strong>{run.original_task}</strong><small>{displayWorkspace(run.workspace)}</small></div>
    <MiniTopology run={run} />
    <TraceIdentity label="Run" value={run.run_id} compact />
    <ArrowUpRight size={14} />
  </a>;
}

function AttentionItem({ icon, title, copy, tone, href }: { icon: React.ReactNode; title: string; copy: string; tone: string; href?: string }) {
  const content = <><span>{icon}</span><div><strong>{title}</strong><p>{copy}</p></div>{href && <ArrowUpRight size={13} />}</>;
  return href ? <a className={`attention-item tone-${tone}`} href={href}>{content}</a> : <div className={`attention-item tone-${tone}`}>{content}</div>;
}

function TopologyNode({ label, detail, status }: { label: string; detail: string; status: string }) {
  return <div className="topology-system-node"><StatusSignal status={status} /><strong>{label}</strong><small>{detail}</small></div>;
}

function TopologyEdge({ state, label }: { state: string; label: string }) {
  return <div className={`topology-system-edge state-${state}`}><span /><code>{label}</code></div>;
}

function MiniRunGraph({ run }: { run: RunSummary }) {
  return <a className="mini-run" href={`#/runs/${encodeURIComponent(run.run_id)}`}>
    <header><code>{run.run_id.slice(0, 8)}</code><StatusSignal status={run.status} /></header>
    <strong>{run.original_task}</strong>
    <MiniTopology run={run} />
    <footer><span>{humanize(run.workflow)}</span><time>{formatDate(run.updated_at)}</time></footer>
  </a>;
}

function MiniTopology({ run }: { run: RunSummary }) {
  const terminal = isTerminal(run.status);
  const failed = ["failed", "rejected", "cancelled"].includes(run.status.toLowerCase());
  const waiting = run.status.toLowerCase().includes("approval");
  const stages = [
    { id: "plan", state: "success" },
    { id: "execute", state: failed ? "danger" : waiting ? "approval" : terminal ? "success" : "active" },
    { id: "verify", state: statusState(run.verifier_status, waiting) },
    { id: "review", state: statusState(run.reviewer_status, waiting) }
  ];
  return <div className="mini-topology" aria-label={`Run topology: ${stages.map((stage) => `${stage.id} ${stage.state}`).join(", ")}`}>{stages.map((stage, index) => <span className={`state-${stage.state}`} key={stage.id}><i />{index < stages.length - 1 && <b />}</span>)}</div>;
}

export function HistoryRow({ run }: { run: RunSummary }) {
  return <a className="table-row" href={`#/runs/${encodeURIComponent(run.run_id)}`}><code>{run.run_id.slice(0, 8)}</code><strong title={run.original_task}>{run.original_task}</strong><span>{humanize(run.workflow)}</span><time>{formatDate(run.updated_at)}</time><StatusSignal status={run.status} /></a>;
}

function statusState(status: string | null | undefined, blocked: boolean): string {
  if (blocked && !status) return "blocked";
  const value = String(status ?? "").toLowerCase();
  if (["passed", "approved", "succeeded"].some((item) => value.includes(item))) return "success";
  if (["failed", "rejected"].some((item) => value.includes(item))) return "danger";
  if (value.includes("running")) return "active";
  return blocked ? "blocked" : "muted";
}

function isTerminal(status: string): boolean {
  return ["succeeded", "completed", "failed", "rejected", "cancelled"].includes(status.toLowerCase());
}
