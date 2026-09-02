import { ChevronDown, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, CircleDot, Pause, Play, Search } from "lucide-react";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import { formatTime, humanize } from "../lib/format";
import type { TimelineRecord } from "./timelineModel";
import { sampleTimeline } from "./timelineModel";

export type TimelineMode = "live" | "manual" | "replay";

export interface TimelinePresentation {
  mode: TimelineMode;
  recordId?: string;
}

export function FlightRecorder({ collapsed, events, onChange, onSelect, onToggle, presentation, terminal = false }: {
  collapsed: boolean;
  events: readonly TimelineRecord[];
  onChange: (presentation: TimelinePresentation) => void;
  onSelect: (record: TimelineRecord) => void;
  onToggle: () => void;
  presentation: TimelinePresentation;
  terminal?: boolean;
}) {
  const [query, setQuery] = useState("");
  const search = useRef<HTMLInputElement>(null);
  const ticks = sampleTimeline(events);
  const selectedIndex = presentation.mode === "live"
    ? Math.max(0, events.length - 1)
    : Math.max(0, events.findIndex((event) => event.id === presentation.recordId));
  const current = events[selectedIndex];
  const liveBacklog = presentation.mode === "live" ? 0 : Math.max(0, events.length - selectedIndex - 1);
  const matches = query ? events.filter((event) => `${event.title} ${event.actor} ${event.status} ${event.summary}`.toLowerCase().includes(query.toLowerCase())) : [];

  function selectIndex(index: number, mode: TimelineMode = presentation.mode === "replay" ? "replay" : "manual") {
    const bounded = Math.max(0, Math.min(events.length - 1, index));
    const record = events[bounded];
    if (!record) return;
    onChange({ mode, recordId: record.id });
    onSelect(record);
  }

  function returnLatest() {
    const record = events.at(-1);
    onChange({ mode: "live", recordId: record?.id });
    if (record) onSelect(record);
  }

  function searchNext() {
    if (!matches.length) return;
    const matchIndex = matches.findIndex((event) => event.id === current?.id);
    const next = matches[(matchIndex + 1) % matches.length];
    selectIndex(events.findIndex((event) => event.id === next.id));
  }

  const keyboard = useEffectEvent((event: KeyboardEvent) => {
    if (isEditableTarget(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "/") {
      event.preventDefault();
      search.current?.focus();
    } else if (event.key.toLowerCase() === "j") {
      event.preventDefault();
      selectIndex(selectedIndex + 1);
    } else if (event.key.toLowerCase() === "k") {
      event.preventDefault();
      selectIndex(selectedIndex - 1);
    }
  });

  useEffect(() => {
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  }, []);

  if (collapsed) {
    return <section className="flight-recorder is-collapsed" aria-label="Event flight recorder"><button type="button" onClick={onToggle}><Play size={12} /><span>Flight recorder</span><strong>{events.length} records</strong><ChevronDown size={13} /></button></section>;
  }

  return <section className={`flight-recorder mode-${presentation.mode}`} aria-label="Event flight recorder">
    <header>
      <div className="recorder-identity"><CircleDot size={13} /><span>Flight recorder</span><strong>{events.length} records</strong></div>
      <div className="recorder-mode" role="group" aria-label="Timeline mode">
        <button className={presentation.mode === "live" ? "is-active" : ""} type="button" onClick={returnLatest}>{presentation.mode === "live" ? <CircleDot size={11} /> : <Play size={11} />}{terminal ? "Latest" : liveBacklog ? `LIVE +${liveBacklog}` : "Live"}</button>
        <button className={presentation.mode === "manual" ? "is-active" : ""} type="button" disabled={!events.length} onClick={() => current && onChange({ mode: "manual", recordId: current.id })}><Pause size={11} /> Inspect</button>
        <button className={presentation.mode === "replay" ? "is-active" : ""} type="button" disabled={!events.length} onClick={() => selectIndex(selectedIndex, "replay")}><Play size={11} /> Replay</button>
      </div>
      <label className="recorder-search"><Search size={12} /><input ref={search} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") searchNext(); }} placeholder="Search run /" />{query && <code>{matches.length}</code>}</label>
      <div className="recorder-steps">
        <button type="button" disabled={!events.length || selectedIndex === 0} onClick={() => selectIndex(0)} aria-label="First event"><ChevronsLeft size={13} /></button>
        <button type="button" disabled={!events.length || selectedIndex === 0} onClick={() => selectIndex(selectedIndex - 1)} aria-label="Previous event"><ChevronLeft size={13} /></button>
        <button type="button" disabled={!events.length || selectedIndex >= events.length - 1} onClick={() => selectIndex(selectedIndex + 1)} aria-label="Next event"><ChevronRight size={13} /></button>
        <button type="button" disabled={!events.length || selectedIndex >= events.length - 1} onClick={() => selectIndex(events.length - 1)} aria-label="Latest event"><ChevronsRight size={13} /></button>
        <button type="button" onClick={onToggle} aria-label="Collapse timeline"><ChevronDown size={13} /></button>
      </div>
    </header>
    <div className="recorder-track">
      <span className="track-line" />
      {ticks.map((tick) => {
        const index = events.findIndex((event) => event.id === tick.record.id);
        const selected = tick.record.id === current?.id;
        return <button className={`timeline-tick source-${tick.record.source} ${selected ? "is-selected" : ""} status-${statusTone(tick.record.status)}`} style={{ left: `${events.length <= 1 ? 0 : (index / (events.length - 1)) * 100}%` }} type="button" key={tick.record.id} onClick={() => selectIndex(index)} aria-label={`${tick.record.title}, ${tick.record.status}, ${formatTime(tick.record.timestamp)}${tick.count > 1 ? `, ${tick.count} grouped records` : ""}`}><i />{tick.count > 1 && <small>{tick.count}</small>}</button>;
      })}
      {current && <span className="timeline-cursor" style={{ left: `${events.length <= 1 ? 0 : (selectedIndex / (events.length - 1)) * 100}%` }} />}
    </div>
    <footer>{current ? <><time>{formatTime(current.timestamp)}</time><span>{current.actor}</span><strong>{current.title}</strong><code>{humanize(current.status)}</code><p>{current.summary}</p></> : <p>No persisted timeline records are available.</p>}</footer>
  </section>;
}

function statusTone(status: string): string {
  const value = status.toLowerCase();
  if (["failed", "rejected", "denied", "cancelled"].some((item) => value.includes(item))) return "danger";
  if (["succeeded", "passed", "approved", "sealed", "completed"].some((item) => value.includes(item))) return "success";
  if (["approval", "waiting", "awaiting"].some((item) => value.includes(item))) return "approval";
  if (["running", "started", "active"].some((item) => value.includes(item))) return "active";
  return "muted";
}

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || target instanceof HTMLElement && target.isContentEditable;
}
