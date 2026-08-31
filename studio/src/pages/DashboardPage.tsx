import { AlertTriangle, ArrowUpRight, CheckCircle2, CircleDot, Cpu, FileCode2, Plus, RadioTower } from "lucide-react";
import type { RunSummary } from "../api/types";
import { EmptyState, PageIntro, StatusSignal, TraceIdentity } from "../components/Primitives";
import { displayWorkspace, formatDate, humanize } from "../lib/format";
import { useStudio } from "../state/StudioContext";

export function DashboardPage() {
  const { runs, providers, doctor, info, streamConnected } = useStudio();
  const active = runs.filter((run) => !isTerminal(run.status));
  const failed = runs.filter((run) => ["failed", "rejected"].includes(run.status.toLowerCase()));
  const successful = runs.filter((run) => ["succeeded", "completed"].includes(run.status.toLowerCase()));
  const changed = runs.reduce((total, run) => total + (run.changed_files?.length ?? 0), 0);
  const readyProviders = providers.filter((provider) => provider.ready);

  return (
    <div className="page dashboard-page">
      <PageIntro eyebrow="Execution graph OS" title="Command deck" action={<a className="button button-primary" href="#/new"><Plus size={15} /> Launch run</a>}>
        Live durable execution state from your authenticated local AgentBus daemon.
      </PageIntro>

      <section className="signal-deck" aria-label="System signals">
        <MetricSignal icon={<CircleDot size={17} />} value={active.length} label="Active runs" accent="cyan" />
        <MetricSignal icon={<CheckCircle2 size={17} />} value={successful.length} label="Completed" accent="emerald" />
        <MetricSignal icon={<AlertTriangle size={17} />} value={failed.length} label="Needs attention" accent="coral" />
        <MetricSignal icon={<FileCode2 size={17} />} value={changed} label="Observed files" accent="amber" />
      </section>

      <div className="dashboard-grid">
        <section className="command-lane">
          <div className="section-heading"><div><p className="section-label">Now executing</p><h2>Durable runs</h2></div><span className={`live-indicator ${streamConnected ? "online" : ""}`}><i />{streamConnected ? "Live" : "Snapshot"}</span></div>
          {active.length ? active.slice(0, 5).map((run, index) => <RunRow key={run.run_id} run={run} index={index} />) : (
            <EmptyState title="The execution lane is clear" action={<a className="text-link" href="#/new">Start a bounded run <ArrowUpRight size={14} /></a>}>
              New runs, policy pauses, verification, and review state will appear here.
            </EmptyState>
          )}
        </section>

        <aside className="runtime-column">
          <section className="runtime-readout">
            <div className="section-heading"><div><p className="section-label">Control plane</p><h2>Runtime pulse</h2></div><Cpu size={18} /></div>
            <dl className="readout-list">
              <div><dt>Daemon</dt><dd><StatusSignal status={doctor?.status ?? "unknown"} /></dd></div>
              <div><dt>Protocol</dt><dd><code>{String(info?.protocol_version ?? "not available")}</code></dd></div>
              <div><dt>Providers ready</dt><dd>{readyProviders.length}/{providers.length}</dd></div>
              <div><dt>Event transport</dt><dd>{streamConnected ? "SSE connected" : "Snapshot fallback"}</dd></div>
            </dl>
            <a className="panel-link" href="#/runtime">Inspect runtime <ArrowUpRight size={14} /></a>
          </section>
          <section className="provider-strip">
            <p className="section-label">Provider routes</p>
            {providers.length ? providers.map((provider) => (
              <div className="provider-row" key={provider.name}><RadioTower size={14} /><span><strong>{humanize(provider.name)}</strong><small>{provider.model ?? provider.message ?? "No model route"}</small></span><StatusSignal status={provider.ready ? "ready" : provider.configured ? "configured" : "offline"} /></div>
            )) : <p className="quiet-copy">No provider routes reported.</p>}
          </section>
        </aside>
      </div>

      <section className="recent-section">
        <div className="section-heading"><div><p className="section-label">Flight recorder</p><h2>Recent run history</h2></div><a className="text-link" href="#/history">View all <ArrowUpRight size={14} /></a></div>
        <div className="history-table compact-table">
          <div className="table-head"><span>Run</span><span>Task</span><span>Workflow</span><span>Updated</span><span>Status</span></div>
          {runs.slice(0, 6).map((run) => <HistoryRow key={run.run_id} run={run} />)}
          {!runs.length && <p className="quiet-copy table-empty">No persisted runs were returned by the daemon.</p>}
        </div>
      </section>
    </div>
  );
}

function MetricSignal({ icon, value, label, accent }: { icon: React.ReactNode; value: number; label: string; accent: string }) {
  return <article className={`metric-signal accent-${accent}`}><span className="metric-icon">{icon}</span><strong>{value}</strong><small>{label}</small><i /></article>;
}

function RunRow({ run, index }: { run: RunSummary; index: number }) {
  return (
    <a className="run-row" href={`#/runs/${encodeURIComponent(run.run_id)}`} style={{ "--row-index": index } as React.CSSProperties}>
      <span className="run-index">{String(index + 1).padStart(2, "0")}</span>
      <span className="run-copy"><strong>{run.original_task}</strong><small title="Canonical workspace hidden for presentation safety">{displayWorkspace(run.workspace)}</small></span>
      <TraceIdentity label="Run" value={run.run_id} compact />
      <StatusSignal status={run.status} />
      <ArrowUpRight size={15} />
    </a>
  );
}

export function HistoryRow({ run }: { run: RunSummary }) {
  return (
    <a className="table-row" href={`#/runs/${encodeURIComponent(run.run_id)}`}>
      <code>{run.run_id.slice(0, 8)}</code>
      <strong title={run.original_task}>{run.original_task}</strong>
      <span>{humanize(run.workflow)}</span>
      <time>{formatDate(run.updated_at)}</time>
      <StatusSignal status={run.status} />
    </a>
  );
}

function isTerminal(status: string): boolean {
  return ["succeeded", "completed", "failed", "rejected", "cancelled"].includes(status.toLowerCase());
}
