import { ArrowDown, ArrowRight, Database, Plus, RefreshCw, Search, X } from "lucide-react";
import { useEffect, useEffectEvent, useState, type CSSProperties } from "react";
import type { RunSummary } from "../api/types";
import { EmptyState, ErrorPanel } from "../components/Primitives";
import { useStudio } from "../state/StudioContext";
import { RunLedgerRecord } from "../components/RunLedgerRecord";
import { FirstRunWelcome } from "../components/FirstRunWelcome";

export function HistoryPage() {
  const { client, eventRevision, runs: recentRuns } = useStudio();
  const initial = new URLSearchParams(window.location.hash.split("?")[1]);
  const [query, setQuery] = useState(initial.get("search") ?? "");
  const [status, setStatus] = useState(initial.get("status") ?? "all");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [cursor, setCursor] = useState<string>();
  const [pageCursor, setPageCursor] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [revision, setRevision] = useState(0);
  const [snapshotVersion, setSnapshotVersion] = useState<number>();
  const [snapshotTime, setSnapshotTime] = useState<string>();
  const stale = snapshotVersion !== undefined && eventRevision !== snapshotVersion;
  const currentEventVersion = useEffectEvent(() => eventRevision);
  useEffect(() => {
    function restoreFilters() {
      if (!window.location.hash.startsWith("#/history")) return;
      const parameters = new URLSearchParams(window.location.hash.split("?")[1]);
      const nextQuery = parameters.get("search") ?? "";
      const nextStatus = parameters.get("status") ?? "all";
      if (nextQuery !== query || nextStatus !== status) {
        setQuery(nextQuery); setStatus(nextStatus); setPageCursor(undefined); setRuns([]); setLoading(true);
      }
    }
    window.addEventListener("hashchange", restoreFilters);
    return () => window.removeEventListener("hashchange", restoreFilters);
  }, [query, status]);
  useEffect(() => {
    if (!client) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      const startedVersion = currentEventVersion();
      setLoading(true);
      void client.history(query, status, pageCursor, { signal: controller.signal, force: true })
        .then((result) => {
          if (controller.signal.aborted) return;
          setRuns((current) => pageCursor ? [...current, ...result.runs.filter((run) => !current.some((old) => old.run_id === run.run_id))] : result.runs);
          setCursor(result.next_cursor ?? undefined);
          setSnapshotVersion((current) => pageCursor ? current ?? startedVersion : startedVersion);
          setSnapshotTime((current) => pageCursor && current ? current : new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }));
          setError(undefined);
        }).catch((reason: unknown) => {
          if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "History unavailable");
        }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [client, query, status, pageCursor, revision]);

  function filter(nextQuery: string, nextStatus: string) {
    setQuery(nextQuery); setStatus(nextStatus); setPageCursor(undefined); setRuns([]); setLoading(true);
    const params = new URLSearchParams(window.location.hash.split("?")[1]);
    params.set("search", nextQuery); params.set("status", nextStatus);
    if (nextStatus !== status) window.location.assign(`#/history?${params}`);
    else {
      window.history.replaceState(null, "", `#/history?${params}`);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }
  }
  return <div className="page history-page">
    <header className="ledger-intro">
      <div><p className="eyebrow"><span className="ledger-registration" aria-hidden="true" />The execution ledger</p><h1>All runs</h1><p className="ledger-lede">Good work leaves a trail.<br /><span>Pick up the intent. Inspect the evidence.</span></p></div>
      <div className="ledger-intro-actions"><span className="ledger-storage"><Database size={14} aria-hidden="true" />Local. Durable. Yours.</span><a className="button button-primary" href="#/new"><Plus size={17} aria-hidden="true" />New task<ArrowRight size={16} aria-hidden="true" /></a></div>
    </header>
    <section className={`ledger-signals ${recentRuns.length ? "" : "is-empty"}`} aria-label="Recent run signals">
      <div className="ledger-signal-note"><span className="section-label">Recent snapshot</span><small>{recentRuns.length} most recently updated {recentRuns.length === 1 ? "record" : "records"}, not the complete history.</small></div>
      {[{ label: "Running", value: "running" }, { label: "Needs approval", value: "waiting_for_approval" }, { label: "Succeeded", value: "succeeded" }, { label: "Failed", value: "failed" }].map((signal) => <button key={signal.value} type="button" aria-pressed={status === signal.value} onClick={() => filter(query, status === signal.value ? "all" : signal.value)}><strong>{recentRuns.filter((run) => run.status === signal.value).length.toString().padStart(2, "0")}</strong><span>{signal.label}<ArrowDown size={12} aria-hidden="true" /></span></button>)}
    </section>
    <div className="history-toolbar">
      <div className="ledger-search"><label htmlFor="history-search">Search history</label><div><Search size={16} aria-hidden="true" /><input id="history-search" type="search" value={query} maxLength={512} onChange={(event) => filter(event.target.value, status)} placeholder="Find a task, repository, or run ID" />{query && <button type="button" aria-label="Clear history search" onClick={() => filter("", status)}><X size={16} aria-hidden="true" /></button>}</div></div>
      <label><span>Status</span><select aria-label="Status" value={status} onChange={(event) => filter(query, event.target.value)}>{["all", "pending", "running", "waiting_for_approval", "waiting_for_review", "succeeded", "failed", "cancelled"].map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
      {(query || status !== "all") && <button className="ledger-reset" type="button" onClick={() => filter("", "all")}>Reset filters</button>}
    </div>
    <div className="ledger-result-bar"><span>{runs.length} {runs.length === 1 ? "run" : "runs"} loaded{cursor ? "; more available" : ""}</span><span>Newest created first <span aria-hidden="true">/</span> Updates never reorder work</span></div>
    {snapshotVersion !== undefined && <div className={`history-snapshot ${stale ? "is-stale" : ""}`}><span role="status">{stale ? "New events arrived; the saved history snapshot may be stale." : "Server-read history snapshot."}{snapshotTime && <small> Read at {snapshotTime}</small>}</span><button disabled={loading} onClick={() => { setPageCursor(undefined); setRevision((value) => value + 1); }}><RefreshCw size={14} className={loading ? "spin" : undefined} aria-hidden="true" />Refresh history</button></div>}
    {error && <><ErrorPanel message={`History snapshot may be stale. ${error}`} /><button onClick={() => setRevision((value) => value + 1)}>Retry history</button></>}
    <ol className="history-records" aria-label="Run records">{runs.map((run, index) => <li key={run.run_id} style={{ "--record-index": Math.min(index, 5) } as CSSProperties}><RunLedgerRecord run={run} /></li>)}</ol>
    {loading ? <><p className="ledger-loading" role="status">Loading history...</p>{!runs.length && <div className="ledger-skeleton" aria-hidden="true">{Array.from({ length: 4 }, (_, index) => <div key={index}><i /><span /><span /></div>)}</div>}</> : !error && !runs.length ? !stale && !query && status === "all" ? <FirstRunWelcome /> : <EmptyState title={stale ? "Saved history snapshot is empty" : "No matching runs"} action={!stale ? <button type="button" onClick={() => filter("", "all")}>Clear filters</button> : undefined}>{stale ? "Refresh history to reconcile new events." : "Try a different task, repository, or status. Your saved work is unchanged."}</EmptyState> : null}
    {!loading && !error && (cursor ? <footer className="ledger-pagination"><span>Your place is saved when you open a run.</span><button onClick={() => setPageCursor(cursor)}>Load more runs<ArrowDown size={15} aria-hidden="true" /></button></footer> : runs.length > 0 && <p className="ledger-end">End of history</p>)}
  </div>;
}
