import { useEffect, useEffectEvent, useState } from "react";
import type { RunSummary } from "../api/types";
import { EmptyState, ErrorPanel } from "../components/Primitives";
import { useStudio } from "../state/StudioContext";
import { HistoryRow } from "./DashboardPage";

export function HistoryPage() {
  const { client, eventRevision } = useStudio();
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
          setSnapshotVersion(startedVersion);
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
    if (nextStatus !== status) window.location.hash = `#/history?${params}`;
    else {
      window.history.replaceState(null, "", `#/history?${params}`);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }
  }
  return <div className="page history-page">
    <h1>All runs</h1><p>Persisted work, newest created first. Updates do not reorder history.</p>
    {snapshotVersion !== undefined && <p className="history-snapshot" role="status">{stale ? "New events arrived; the saved history snapshot may be stale." : "Server-read history snapshot."} <button disabled={loading} onClick={() => { setPageCursor(undefined); setRevision((value) => value + 1); }}>Refresh history</button></p>}
    <div className="history-toolbar">
      <label>Search history<input value={query} maxLength={512} onChange={(event) => filter(event.target.value, status)} placeholder="Task, workspace, or run ID" /></label>
      <label><span>Status</span><select aria-label="Status" value={status} onChange={(event) => filter(query, event.target.value)}>{["all", "pending", "running", "waiting_for_approval", "waiting_for_review", "succeeded", "failed", "cancelled"].map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
    </div>
    {error && <><ErrorPanel message={`History snapshot may be stale. ${error}`} /><button onClick={() => setRevision((value) => value + 1)}>Retry history</button></>}
    {runs.map((run) => <HistoryRow key={run.run_id} run={run} />)}
    {loading ? <p role="status">Loading history...</p> : !error && !runs.length ? <EmptyState title={stale ? "Saved history snapshot is empty" : query || status !== "all" ? "No matching runs" : "No runs yet"} action={!stale && !query && status === "all" ? <div className="history-invitation"><strong>Your first piece<br />of inspectable work.</strong><a href="#/new">Bring your intent <span aria-hidden="true">/</span></a><a href="#/workshop">Explore the workshop <span aria-hidden="true">/</span></a></div> : undefined}>{stale ? "Refresh history to reconcile new events." : "Create a task to begin."}</EmptyState> : null}
    {!loading && !error && (cursor ? <button onClick={() => setPageCursor(cursor)}>Load more runs</button> : runs.length > 0 && <p>End of history</p>)}
  </div>;
}
