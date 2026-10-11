import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode, type RefObject } from "react";
import type { RunSummary } from "../api/types";
import { CommandPalette } from "./CommandPalette";
import { ArrowDownLeft, Compass, History, LogOut, Plus, Radio, SlidersHorizontal, X } from "lucide-react";
import { StatusSignal } from "./Primitives";
import { displayWorkspace, shortId } from "../lib/format";
import { FolioMemoryContext, FolioWorkingSet, folioLocation, ledgerLocation, saveResumeFolio, useReadingPosition } from "../state/folioNavigation";

export function FolioShell({ runs, selected, phase, drawer, children, onDisconnect }: {
  runs: RunSummary[]; selected?: string; phase: string; drawer: ReactNode;
  children: ReactNode; onDisconnect: () => void;
}) {
  const [memory] = useState(() => new FolioWorkingSet(runs.map((run) => run.run_id)));
  useSyncExternalStore(memory.subscribe, memory.snapshot, memory.snapshot);
  const paper = useRef<HTMLElement>(null);
  const [theme, setTheme] = useState(() => {
    try { const value = localStorage.getItem("syndra.studio.theme"); return value === "light" || value === "dark" || value === "system" ? value : "light"; }
    catch { return "light"; }
  });
  const [expanded, setExpanded] = useState(false);
  const edge = useRef<HTMLAnchorElement>(null);
  const drawerHeading = useRef<HTMLHeadingElement>(null);
  const current = runs.find((run) => run.run_id === selected);
  const drawerOpen = new URLSearchParams(window.location.hash.split("?")[1]).has("drawer");
  const location = folioLocation(window.location.hash);
  const href = location && location.id === selected ? location.href : selected ? `#/runs/${encodeURIComponent(selected)}` : undefined;
  const locationId = location?.id;
  const validatedId = current?.run_id;
  const ledgerHref = ledgerLocation(window.location.hash);
  useLayoutEffect(() => {
    if (!selected || !href || locationId !== selected) return;
    memory.visit(selected, href);
    if (validatedId === selected) saveResumeFolio(href);
  }, [selected, href, locationId, validatedId, memory]);
  useLayoutEffect(() => { if (ledgerHref) memory.visitLedger(ledgerHref); }, [ledgerHref, memory]);
  useLayoutEffect(() => { memory.receiveSummaries(runs); }, [runs, selected, memory]);
  useLayoutEffect(() => { if (drawerOpen) drawerHeading.current?.focus({ preventScroll: true }); }, [drawerOpen]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("syndra.studio.theme", theme); } catch { /* Session appearance still works when storage is unavailable. */ }
  }, [theme]);
  const visible = memory.open;
  const attention = runs.filter((run) => ["waiting_for_approval", "failed", "waiting_for_review"].includes(run.status));
  function drawerLink(show: boolean) {
    const [path, raw] = window.location.hash.split("?");
    const query = new URLSearchParams(raw);
    if (show) query.set("drawer", "connections"); else query.delete("drawer");
    return `${path || "#/"}${query.size ? `?${query}` : ""}`;
  }
  function closeDrawer() {
    window.location.hash = drawerLink(false);
    setExpanded(false);
    edge.current?.focus();
  }
  function closeFolio(id: string) {
    const next = memory.close(id);
    if (selected === id) { saveResumeFolio("#/history"); window.location.hash = next; }
  }
  return <FolioMemoryContext.Provider value={memory}><div className="folio-desk">
    <a className="skip-link" href="#main-content" onClick={(event) => { event.preventDefault(); paper.current?.focus({ preventScroll: true }); }}>Skip to work</a>
    <header className="workspace-clasp">
      <a href="#/" className="folio-brand" aria-label="Syndra Studio"><img src={`${import.meta.env.BASE_URL}syndra-mark.svg`} alt="" /><strong>Syndra</strong><span>Studio</span></a>
      <span className="clasp-workspace" title={current?.workspace}><span>Workspace</span><strong>{current ? displayWorkspace(current.workspace) : selected ? "Run workspace unavailable" : "Local workspace"}</strong></span>
      <span className={`connection-indicator phase-${phase}`} role="status"><Radio size={14} />{phase === "connected" ? "Connected" : phase.replaceAll("_", " ")}</span>
      <CommandPalette currentRun={current} />
    </header>
    <aside className="work-queue" aria-label="Working queue">
    <div className="queue-heading"><span className="eyebrow">Working set</span><span>{visible.length.toString().padStart(2, "0")}</span></div>
    <p className="queue-caption">Open work. Not the complete history.</p>
    <nav className="open-run-shelf" aria-label="Open runs">
      {visible.map((id) => {
        const run = runs.find((item) => item.run_id === id) ?? memory.summary(id);
        return <div className={`run-object ${id === selected ? "selected" : ""}`} key={id}>
          <a href={id === selected ? href : memory.href(id)} aria-current={id === selected ? "page" : undefined}>
            <span className="queue-run-meta"><code>{shortId(id, 8)}</code><StatusSignal status={run?.status} label={run?.status.replaceAll("_", " ") ?? "Open run"} /></span>
            <strong>{run?.original_task ?? id}</strong>
            <small>{run ? displayWorkspace(run.workspace) : "Workspace unavailable"}</small>
          </a>
          <button aria-label={`Close ${run?.original_task ?? id} from shelf`} onClick={() => closeFolio(id)}><X size={14} /></button>
        </div>;
      })}
      {!visible.length && <p className="queue-empty">No open runs. Start a task or reopen persisted work.</p>}
      {memory.open.length > 0 && <div className="mobile-working-set"><label className="more-open-runs">Open runs<select value={selected && memory.open.includes(selected) ? selected : ""} onChange={(event) => { if (event.target.value) window.location.hash = memory.href(event.target.value); }}><option value="">Choose run</option>{memory.open.map((id) => { const item = runs.find((run) => run.run_id === id) ?? memory.summary(id); return <option key={id} value={id}>{item ? `${item.original_task} / ${item.status.replaceAll("_", " ")}` : id}</option>; })}</select></label>{selected && memory.open.includes(selected) && <button type="button" aria-label="Close selected run from working queue" onClick={() => closeFolio(selected)}><X size={16} /></button>}</div>}
    </nav>
    <footer className="workspace-edge">
      <a href="#/new" className="edge-new"><Plus size={17} />New task</a>
      <a href={memory.ledgerHref}><History size={17} />All runs</a>
      <a href="#/workshop" className="edge-explore"><Compass size={17} />Explore Syndra</a>
      <label>Needs attention <select value="" onChange={(event) => { if (event.target.value) window.location.hash = `#/runs/${encodeURIComponent(event.target.value)}`; }}><option value="">{attention.length} in recent work</option>{attention.map((run) => <option key={run.run_id} value={run.run_id}>{run.status}: {run.original_task}</option>)}</select></label>
      <a ref={edge} href={drawerLink(!drawerOpen)} aria-expanded={drawerOpen}><SlidersHorizontal size={17} />Workspace<ArrowDownLeft size={14} /></a>
      <button onClick={onDisconnect}><LogOut size={16} />Disconnect</button>
      <details className="mobile-attention"><summary>Needs attention: {attention.length} in recent work</summary><label>Choose work needing attention<select value="" onChange={(event) => { if (!event.target.value) return; const disclosure = event.currentTarget.closest("details"); if (disclosure) { disclosure.open = false; disclosure.querySelector("summary")?.focus(); } window.location.hash = `#/runs/${encodeURIComponent(event.target.value)}`; }}><option value="">Choose a run</option>{attention.map((run) => <option key={run.run_id} value={run.run_id}>{run.status.replaceAll("_", " ")}: {run.original_task}</option>)}</select></label></details>
      <p className="queue-footnote">Local by design. Evidence by default.</p>
    </footer>
    </aside>
    <FolioPaper readingKey={href ?? ledgerHref ?? window.location.hash.split("?")[0]} viewport={paper}>{children}</FolioPaper>
    {drawerOpen && <section className={`workspace-drawer ${expanded ? "expanded" : ""}`} aria-label="Workspace" onKeyDown={(event) => { if (event.key === "Escape") closeDrawer(); }}>
      <header><h2 ref={drawerHeading} tabIndex={-1}>Workspace</h2><button onClick={() => setExpanded((value) => !value)}>{expanded ? "Fold workspace view" : "Expand workspace view"}</button><button onClick={closeDrawer}>Return to run</button></header>
      {drawer}
      <label>Appearance <select value={theme} onChange={(event) => setTheme(event.target.value)}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
    </section>}
  </div></FolioMemoryContext.Provider>;
}

function FolioPaper({ readingKey, viewport, children }: { readingKey?: string; viewport: RefObject<HTMLElement | null>; children: ReactNode }) {
  useReadingPosition(readingKey, viewport);
  return <main ref={viewport} tabIndex={-1} id="main-content" className="folio-paper">{children}</main>;
}
