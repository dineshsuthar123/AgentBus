import { CornerDownRight, RotateCcw, ShieldAlert } from "lucide-react";
import type { AttemptSummary, TaskSummary } from "../api/types";
import { humanize } from "../lib/format";
import { StatusSignal } from "./Primitives";

export function AttemptStack({ tasks, attempts = [], report }: {
  tasks: TaskSummary[];
  attempts?: AttemptSummary[];
  report?: Record<string, unknown>;
}) {
  const taskFailures = Array.isArray(report?.task_failures)
    ? report.task_failures as Array<Record<string, unknown>>
    : [];
  const reportAttempts = report?.attempts_per_task && typeof report.attempts_per_task === "object"
    ? report.attempts_per_task as Record<string, number>
    : {};
  const visible = tasks.filter((task) => Math.max(
    task.attempts,
    reportAttempts[task.task_id] ?? 0,
    attempts.filter((attempt) => attempt.task_id === task.task_id).length
  ) > 0);
  if (!visible.length) return <p className="quiet-copy">Attempts appear here when the durable graph begins execution.</p>;
  return (
    <div className="attempt-stack">
      {visible.map((task) => {
        const records = attempts.filter((attempt) => attempt.task_id === task.task_id);
        const count = Math.max(task.attempts, reportAttempts[task.task_id] ?? 0, records.length);
        const failure = taskFailures.find((item) => item.task_id === task.task_id);
        const evidenceRecords = records.filter((record) => record.retry_evidence);
        return (
          <article className="attempt-branch" key={task.task_id}>
            <div className="attempt-heading"><div><span className="section-label">{task.task_id}</span><h3>{task.title}</h3></div><StatusSignal status={task.status} /></div>
            <div className="attempt-layers">
              {Array.from({ length: count }, (_, index) => {
                const number = index + 1;
                const record = records.find((attempt) => attempt.attempt_number === number);
                const current = number === count;
                const failed = record?.status === "failed" || (!record && !current && count > 1);
                return (
                  <div className={`attempt-layer ${current ? "is-current" : "is-prior"}`} key={number}>
                    <span className="attempt-number">{number}</span>
                    <div><strong>Attempt {number}</strong><p>{record ? humanize(record.status) : failed ? "Candidate retained with failure evidence" : current ? humanize(task.status) : "Completed"}</p></div>
                    {failed ? <ShieldAlert size={16} /> : current && count > 1 ? <RotateCcw size={16} /> : <CornerDownRight size={16} />}
                  </div>
                );
              })}
            </div>
            {evidenceRecords.map((record) => {
              const evidence = record.retry_evidence!;
              const correctiveItems = (evidence.diagnostics.reviewer_issues ?? []).concat(evidence.diagnostics.required_fixes ?? []);
              return <div className="retry-evidence" key={`${record.attempt_id}-evidence`}><RotateCcw size={14} /><span><strong>RetryEvidence {evidence.source_disposition ? humanize(evidence.source_disposition) : "sealed"}</strong>{evidence.diagnostics.summary || String(failure?.message ?? task.failure_message ?? "Corrective evidence is attached to this immutable attempt.")}{correctiveItems.length > 0 && <small>{correctiveItems.join(" | ")}</small>}</span></div>;
            })}
            {count > 1 && evidenceRecords.length === 0 && <div className="retry-evidence"><RotateCcw size={14} /><span><strong>RetryEvidence carried forward</strong>{String(failure?.message ?? task.failure_message ?? "Verifier and reviewer observations remain attached to the durable attempt history.")}</span></div>}
          </article>
        );
      })}
    </div>
  );
}
