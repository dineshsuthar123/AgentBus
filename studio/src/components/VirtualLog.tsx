import { Clipboard, ListFilter, Pause, Play, Search, StepForward } from "lucide-react";
import { useDeferredValue, useMemo, useState } from "react";
import { formatTime, sanitizeDisplayText } from "../lib/format";
import { VirtualList } from "./VirtualList";
import type { StudioLogLevel, StudioLogRecord } from "./logModel";

export const MAX_LOG_RECORDS = 50_000;

export function VirtualLog({ height = 300, records }: { height?: number; records: readonly StudioLogRecord[] }) {
  const [level, setLevel] = useState<"all" | StudioLogLevel>("all");
  const [query, setQuery] = useState("");
  const [following, setFollowing] = useState(true);
  const [selectedId, setSelectedId] = useState<string>();
  const [scrollRevision, setScrollRevision] = useState(0);
  const [announcement, setAnnouncement] = useState("");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const bounded = useMemo(() => records.slice(-MAX_LOG_RECORDS), [records]);
  const filtered = useMemo(() => bounded.filter((record) => {
    if (level !== "all" && record.level !== level) return false;
    if (!deferredQuery) return true;
    return `${record.message} ${record.source} ${record.sequence ?? ""}`.toLowerCase().includes(deferredQuery);
  }), [bounded, deferredQuery, level]);
  const selectedIndex = Math.max(0, filtered.findIndex((record) => record.id === selectedId));
  const selected = filtered.find((record) => record.id === selectedId);
  const scrollTarget = filtered.length ? following ? filtered.length - 1 : selected ? selectedIndex : 0 : undefined;

  function choose(index: number) {
    const record = filtered[index];
    if (!record) return;
    setSelectedId(record.id);
    setFollowing(false);
    setScrollRevision((value) => value + 1);
  }

  function keyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return;
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const current = selected ? selectedIndex : event.key === "ArrowDown" ? -1 : filtered.length;
    choose(Math.max(0, Math.min(filtered.length - 1, current + (event.key === "ArrowDown" ? 1 : -1))));
  }

  function toggleFollow() {
    if (following) setSelectedId(filtered.at(-1)?.id);
    setFollowing(!following);
    setScrollRevision((value) => value + 1);
  }

  function jumpLatest() {
    const latest = filtered.at(-1);
    if (!latest) return;
    setSelectedId(latest.id);
    setFollowing(true);
    setScrollRevision((value) => value + 1);
  }

  async function copySelected() {
    if (!selected || !navigator.clipboard) {
      setAnnouncement("Clipboard unavailable.");
      return;
    }
    try {
      await navigator.clipboard.writeText(logLine(selected));
      setAnnouncement("Selected log record copied.");
    } catch {
      setAnnouncement("Selected log record could not be copied.");
    }
  }

  return <section className="virtual-log" aria-label="Runtime event log" onKeyDown={keyDown}>
    <div className="virtual-log-toolbar">
      <label><ListFilter size={12} /><span className="sr-only">Log level</span><select aria-label="Log level" value={level} onChange={(event) => setLevel(event.target.value as "all" | StudioLogLevel)}><option value="all">All levels</option><option value="error">Errors</option><option value="warning">Warnings</option><option value="info">Info</option><option value="debug">Debug</option></select></label>
      <label className="log-search"><Search size={12} /><span className="sr-only">Search logs</span><input aria-label="Search logs" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search bounded events" /></label>
      <button type="button" aria-pressed={!following} onClick={toggleFollow}>{following ? <Pause size={12} /> : <Play size={12} />}{following ? "Pause follow" : "Resume follow"}</button>
      <button type="button" onClick={() => void copySelected()} disabled={!selected}><Clipboard size={12} /> Copy selected</button>
      <button type="button" onClick={jumpLatest} disabled={!filtered.length}><StepForward size={12} /> Jump latest</button>
    </div>
    <div className="virtual-log-summary"><span>{filtered.length.toLocaleString()} visible logical records</span><span>{records.length > MAX_LOG_RECORDS ? `${(records.length - MAX_LOG_RECORDS).toLocaleString()} older records outside the bounded view` : `${bounded.length.toLocaleString()} retained`}</span></div>
    {filtered.length ? <VirtualList ariaLabel="Virtualized runtime records" className="virtual-log-list" height={height} itemCount={filtered.length} overscan={10} rowHeight={22} scrollRevision={scrollRevision} scrollToIndex={scrollTarget} renderItem={(index) => {
      const record = filtered[index];
      return <button className={`virtual-log-row level-${record.level} ${record.id === selectedId ? "is-selected" : ""}`} type="button" aria-label={`Select log ${record.sequence ?? index + 1}`} onClick={() => choose(index)}><time>{formatTime(record.timestamp)}</time><span>{record.level}</span><code>{record.source}</code><strong>{record.message}</strong></button>;
    }} /> : <div className="virtual-log-empty">No retained records match this bounded view.</div>}
    <div className="sr-only" aria-live="polite">{announcement}</div>
  </section>;
}

function logLine(record: StudioLogRecord): string {
  return sanitizeDisplayText(`${record.timestamp} ${record.level.toUpperCase()} ${record.source} ${record.message}`, 2_000);
}
