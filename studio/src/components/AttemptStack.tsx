import { CornerDownRight, RotateCcw, ShieldAlert } from "lucide-react";
import type { TaskSummary } from "../api/types";
import { humanize } from "../lib/format";
import { StatusSignal } from "./Primitives";

export function AttemptStack({ tasks, report }: { tasks: TaskSummary[]; report?: Record<string, unknown> }) {
  const taskFailures = Array.isArray(report?.task_failures) ? report.task_failures as Array<Record<string, unknown>> : [];
  const attempts = report?.attempts_per_task && typeof report.attempts_per_task === "object"
    ? report.attempts_per_task as Record<string, number>
    : {};
  const visible = tasks.filter((task) => Math.max(task.attempts, attempts[task.task_id] ?? 0) > 0);
  if (!visible.length) return <p className="quiet-copy">Attempts appear here when the durable graph begins execution.</p>;
  return (
    <div className="attempt-stack">
      {visible.map((task) => {
        const count = Math.max(task.attempts, attempts[task.task_id] ?? 0);
        const failure = taskFailures.find((item) => item.task_id === task.task_id);
        return (
          <article className="attempt-branch" key={task.task_id}>
            <div className="attempt-heading"><div><span className="section-label">{task.task_id}</span><h3>{task.title}</h3></div><StatusSignal status={task.status} /></div>
            <div className="attempt-layers">
              {Array.from({ length: count }, (_, index) => {
                const number = index + 1;
                const current = number === count;
                const failed = !current && count > 1;
                return (
                  <div className={`attempt-layer ${current ? "is-current" : "is-prior"}`} key={number}>
                    <span className="attempt-number">{number}</span>
                    <div><strong>Attempt {number}</strong><p>{failed ? "Candidate retained with failure evidence" : current ? humanize(task.status) : "Completed"}</p></div>
                    {failed ? <ShieldAlert size={16} /> : current && count > 1 ? <RotateCcw size={16} /> : <CornerDownRight size={16} />}
                  </div>
                );
              })}
            </div>
            {count > 1 && <div className="retry-evidence"><RotateCcw size={14} /><span><strong>RetryEvidence carried forward</strong>{String(failure?.message ?? task.failure_message ?? "Verifier and reviewer observations remain attached to the durable attempt history.")}</span></div>}
          </article>
        );
      })}
    </div>
  );
}
