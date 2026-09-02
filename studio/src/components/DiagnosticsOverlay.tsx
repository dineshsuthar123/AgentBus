import { Activity, Database, RadioTower, X } from "lucide-react";
import { Profiler, useEffect, useMemo, useState } from "react";
import { useEventDiagnostics, useStudio } from "../state/StudioContext";
import { VirtualLog } from "./VirtualLog";
import { eventLogRecords } from "./logModel";

export function DiagnosticsOverlay() {
  if (!import.meta.env.DEV) return null;
  return <DevelopmentDiagnostics />;
}

let diagnosticCommitCount = 0;

function DevelopmentDiagnostics() {
  const studio = useStudio();
  const snapshot = useEventDiagnostics();
  const [open, setOpen] = useState(false);
  const logs = useMemo(() => eventLogRecords(snapshot.events), [snapshot.events]);
  const cache = studio.client?.queryCacheSummary();
  const memory = (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "d") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return <Profiler id="studio-diagnostics" onRender={() => { diagnosticCommitCount += 1; }}><>
    <button className="diagnostics-trigger" type="button" onClick={() => setOpen(true)} aria-label="Open development diagnostics"><Activity size={12} /> DEV</button>
    {open && <aside className="diagnostics-overlay" aria-label="Development diagnostics">
      <header><div><span>Local observability</span><strong>Studio diagnostics</strong></div><button type="button" onClick={() => setOpen(false)} aria-label="Close development diagnostics"><X size={14} /></button></header>
      <div className="diagnostic-status"><RadioTower size={13} /><strong>{studio.streamStatus.phase}</strong><span>cursor {studio.streamStatus.cursor || 0}</span><span>{studio.streamStatus.reconnectCount} reconnects</span></div>
      <dl className="diagnostic-grid">
        <Metric label="Events / sec" value={snapshot.metrics.eventsPerSecond} />
        <Metric label="Retained" value={snapshot.metrics.size} />
        <Metric label="Buffered" value={snapshot.metrics.buffered} />
        <Metric label="Duplicates" value={snapshot.metrics.duplicatesDropped} />
        <Metric label="Out of order" value={snapshot.metrics.outOfOrderDropped} />
        <Metric label="Evicted" value={snapshot.metrics.evicted} />
        <Metric label="Event batches" value={snapshot.metrics.batches} />
        <Metric label="Profiler commits" value={diagnosticCommitCount} />
      </dl>
      <div className="diagnostic-cache"><Database size={13} /><span>Query cache</span><code>{cache ? `${cache.cachedReads} cached / ${cache.inflightReads} inflight / ${cache.cacheHits} hits / ${cache.deduplicatedReads} deduped` : "Client unavailable"}</code><small>{memory ? `${formatBytes(memory.usedJSHeapSize)} JS heap (browser reported)` : "Browser heap metrics unavailable"}</small></div>
      <VirtualLog records={logs} height={220} />
      <footer>Development build only. No external telemetry or sensitive request payloads.</footer>
    </aside>}
  </></Profiler>;
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div><dt>{label}</dt><dd>{value.toLocaleString()}</dd></div>;
}

function formatBytes(value: number): string {
  return `${(value / 1_048_576).toFixed(1)} MiB`;
}
