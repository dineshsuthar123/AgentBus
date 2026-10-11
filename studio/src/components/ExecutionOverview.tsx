import { useState } from "react";
import { ArrowUpRight, Box, CheckCheck, FileCode2, Fingerprint, ListTree, ShieldCheck } from "lucide-react";
import type { RunBundle } from "../api/runBundle";
import type { InspectorView } from "../observatory/selection";
import { StatusSignal } from "./Primitives";
import { RibbonCore } from "./RibbonCore";

export function ExecutionOverview({ bundle, onView, onTask }: { bundle: RunBundle; onView: (view: InspectorView) => void; onTask: (id: string) => void }) {
  const [showObject, setShowObject] = useState(true);
  const [inspection, setInspection] = useState(2);
  const tasks = bundle.tasks.tasks;
  const completed = tasks.filter((task) => task.status === "succeeded").length;
  const layers = [
    { view: "planner", label: "Plan", value: `${tasks.length} persisted tasks`, icon: ListTree },
    { view: "source", label: "Changes", value: bundle.changes ? `${bundle.changes.changes.length} observed files` : "Unavailable", icon: FileCode2 },
    { view: "verifier", label: "Verification", value: bundle.run.verifier_status?.replaceAll("_", " ") ?? "Not run", icon: ShieldCheck },
    { view: "review", label: "Final review", value: bundle.run.reviewer_status?.replaceAll("_", " ") ?? "Not run", icon: CheckCheck },
    { view: "evidence", label: "Evidence", value: bundle.trace ? `${bundle.trace.event_count} captured events` : "Not available", icon: Fingerprint },
  ] as const;
  return <section className="execution-overview" aria-label="Execution overview">
    <header><div><p className="eyebrow">Intent / implementation / evidence</p><h2>Your execution, inspectable.</h2></div><button type="button" aria-pressed={showObject} onClick={() => setShowObject((value) => !value)}><Box size={16} />{showObject ? "Hide object" : "Show object"}</button></header>
    <div className={`overview-body ${showObject ? "with-object" : ""}`}>
      {showObject && <RibbonCore compact initiallyPaused selected={inspection} />}
      <div className="inspection-index"><p>Five lenses, not sequential steps. Orange identifies inspection, not success.</p>{layers.map(({ view, label, value, icon: Icon }, index) => <button key={view} data-selected={inspection === index} aria-label={`${label}: ${value}`} onFocus={() => setInspection(index)} onMouseEnter={() => setInspection(index)} onClick={() => onView(view)}><span className="inspection-number" aria-hidden="true">0{index + 1}</span><Icon size={17} /><strong>{label}</strong><span>{value}</span><ArrowUpRight size={15} /></button>)}</div>
    </div>
    <section className="task-register" aria-label="Persisted task register"><header><h3>Implementation tasks</h3><span>{completed} / {tasks.length} succeeded</span></header>{tasks.length ? tasks.slice(0, 100).map((task) => <button key={task.task_id} aria-label={`${task.task_id}: ${task.title}. ${task.status.replaceAll("_", " ")}`} onClick={() => onTask(task.task_id)}><code>{task.task_id}</code><strong>{task.title}</strong><StatusSignal status={task.status} /><ArrowUpRight size={15} /></button>) : <p>No task specification has been persisted yet.</p>}{tasks.length > 100 && <p>The first 100 tasks are shown here. The complete dependency graph remains available below.</p>}</section>
  </section>;
}
