import { Filter, History, Search } from "lucide-react";
import { useDeferredValue, useState } from "react";
import { EmptyState, PageIntro } from "../components/Primitives";
import { humanize } from "../lib/format";
import { useStudio } from "../state/StudioContext";
import { HistoryRow } from "./DashboardPage";

export function HistoryPage() {
  const { runs } = useStudio();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const deferredQuery = useDeferredValue(query.toLowerCase());
  const statuses = [...new Set(runs.map((run) => run.status))].sort();
  const filtered = runs.filter((run) => {
    const matchesStatus = status === "all" || run.status === status;
    const haystack = `${run.run_id} ${run.original_task} ${run.workspace} ${run.workflow}`.toLowerCase();
    return matchesStatus && haystack.includes(deferredQuery);
  });

  return (
    <div className="page history-page">
      <PageIntro eyebrow="Durable memory" title="Run history">Every row is read from persisted control-plane state; successful terminal tasks remain immutable across resume.</PageIntro>
      <div className="history-toolbar">
        <label className="search-field"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search task, workspace, or run ID" /></label>
        <label className="select-field"><Filter size={15} /><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All statuses</option>{statuses.map((item) => <option value={item} key={item}>{humanize(item)}</option>)}</select></label>
        <span className="result-count">{filtered.length} / {runs.length} runs</span>
      </div>
      {filtered.length ? <div className="history-table"><div className="table-head"><span>Run</span><span>Task</span><span>Workflow</span><span>Updated</span><span>Status</span></div>{filtered.map((run) => <HistoryRow run={run} key={run.run_id} />)}</div> : <EmptyState title="No runs match this view"><History size={17} /> Adjust the query or status filter to inspect persisted history.</EmptyState>}
    </div>
  );
}
