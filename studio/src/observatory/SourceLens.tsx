import { CheckCheck, FileCode2, FileLock2, Filter, LoaderCircle, Search, Wand2, X } from "lucide-react";
import { useDeferredValue, useEffect, useState } from "react";
import type { StudioClient } from "../api/client";
import type { ChangeSummary } from "../api/types";
import type { RunBundle } from "../api/runBundle";
import { StatusSignal } from "../components/Primitives";
import { humanize } from "../lib/format";
import type { TimelineRecord } from "./timelineModel";
import { VirtualDiff } from "./VirtualDiff";

type SourceFilter = "all" | "review" | "generated" | "excluded";

interface DiffState {
  diff: string;
  error?: string;
  key: string;
  truncated?: boolean;
}

export function SourceLens({ bundle, client, highlightTaskId, initialPath, onClose, onLocateTask, onSelectFile, timeline }: {
  bundle: RunBundle;
  client?: StudioClient;
  highlightTaskId?: string;
  initialPath?: string;
  onClose: () => void;
  onLocateTask: (taskId: string) => void;
  onSelectFile: (path: string) => void;
  timeline: readonly TimelineRecord[];
}) {
  const changes = bundle.changes?.changes ?? [];
  const [path, setPath] = useState(() => initialPath ?? preferredPath(changes));
  const [filter, setFilter] = useState<SourceFilter>("all");
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.toLowerCase());
  const [diffState, setDiffState] = useState<DiffState>(() => ({
    diff: bundle.diff?.diff ?? "",
    key: "bundle",
    truncated: bundle.diff?.truncated
  }));
  const selected = changes.find((change) => change.path === path);
  const requestKey = `${bundle.run.run_id}:${path}`;
  const filtered = changes.filter((change) => matchesFilter(change, filter) && change.path.toLowerCase().includes(deferredQuery));

  useEffect(() => {
    if (!client || !path) return;
    const controller = new AbortController();
    void client.diff(bundle.run.run_id, path, { signal: controller.signal })
      .then((response) => {
        if (!controller.signal.aborted) setDiffState({ diff: response.diff, key: requestKey, truncated: response.truncated });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setDiffState({ diff: "", error: error instanceof Error ? error.message : "Diff unavailable.", key: requestKey });
      });
    return () => controller.abort();
  }, [bundle.run.run_id, client, path, requestKey]);

  function selectFile(nextPath: string) {
    setPath(nextPath);
    onSelectFile(nextPath);
  }

  const task = selected?.task_id ? bundle.tasks.tasks.find((item) => item.task_id === selected.task_id) : undefined;
  const attempts = selected?.task_id ? bundle.attempts?.attempts.filter((attempt) => attempt.task_id === selected.task_id) ?? [] : [];
  const activity = selected ? timeline.filter((record) => record.taskId === selected.task_id || record.summary.includes(selected.path)) : [];
  const currentDiff = diffState.key === requestKey || !client ? diffState : undefined;

  return <section className="source-lens" aria-label="Source lens">
    <header><div><span className="observatory-label">Source lens</span><strong>Repository / execution cross-link</strong></div><button type="button" onClick={onClose} aria-label="Close source lens"><X size={14} /></button></header>
    <div className="source-lens-grid">
      <aside className="source-tree">
        <div className="source-tools"><label><Search size={12} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter files" /></label><div><Filter size={11} />{(["all", "review", "generated", "excluded"] as const).map((value) => <button className={filter === value ? "is-active" : ""} type="button" key={value} onClick={() => setFilter(value)}>{value}</button>)}</div></div>
        <div className="source-files" role="list" aria-label="Observed files">{filtered.map((change) => <FileRow change={change} key={change.path} selected={change.path === path} related={Boolean(highlightTaskId && change.task_id === highlightTaskId)} onClick={() => selectFile(change.path)} />)}{!filtered.length && <p>No files match this bounded view.</p>}</div>
      </aside>
      <div className="source-detail">
        {selected ? <>
          <div className="source-detail-head"><div><code>{selected.path}</code><span>{selected.classification}</span></div><StatusSignal status={selected.status} /></div>
          <div className="file-activity">
            <span><small>Observed change</small><strong>{selected.binary ? "Binary" : `+${selected.additions ?? 0} / -${selected.deletions ?? 0}`}</strong></span>
            <span><small>Task link</small><strong>{task?.title ?? "Not persisted"}</strong></span>
            <span><small>Attempt link</small><strong>{attempts.length ? `${attempts.length} task attempts; exact writer not persisted` : "Not persisted"}</strong></span>
            <span><small>Verifier</small><strong>{task?.verifier_status ? humanize(task.verifier_status) : "Not reported"}</strong></span>
          </div>
          <div className="source-crosslinks"><span>{activity.length} related persisted records</span>{task && <button type="button" onClick={() => onLocateTask(task.task_id)}><CheckCheck size={12} /> Locate execution</button>}</div>
          {currentDiff?.error ? <div className="source-diff-error" role="alert">{currentDiff.error}</div> : currentDiff ? <VirtualDiff key={`${requestKey}:${currentDiff.diff.length}`} diff={currentDiff.diff} truncated={currentDiff.truncated} onSelectHunk={() => task && onLocateTask(task.task_id)} /> : <div className="diff-processing"><LoaderCircle className="spin" size={14} /> Loading bounded file diff</div>}
        </> : <div className="source-empty"><FileCode2 size={19} /><strong>No observed file selected</strong><span>Choose a repository change to inspect its persisted classification and bounded diff.</span></div>}
      </div>
    </div>
  </section>;
}

function FileRow({ change, onClick, related, selected }: { change: ChangeSummary; onClick: () => void; related: boolean; selected: boolean }) {
  const Icon = change.generated ? Wand2 : change.classification.includes("excluded") ? FileLock2 : FileCode2;
  return <div role="listitem"><button className={`${selected ? "is-selected" : ""} ${related ? "is-related" : ""}`} type="button" onClick={onClick}><Icon size={13} /><code>{change.path}</code><span>{change.generated ? "generated" : change.classification}</span><small>{change.binary ? "bin" : `+${change.additions ?? 0} -${change.deletions ?? 0}`}</small></button></div>;
}

function preferredPath(changes: ChangeSummary[]): string {
  return changes.find((change) => !change.generated && !change.classification.includes("excluded"))?.path ?? changes[0]?.path ?? "";
}

function matchesFilter(change: ChangeSummary, filter: SourceFilter): boolean {
  if (filter === "all") return true;
  if (filter === "generated") return Boolean(change.generated);
  return change.classification.includes(filter);
}
