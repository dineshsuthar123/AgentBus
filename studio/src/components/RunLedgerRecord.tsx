import { ArrowUpRight } from "lucide-react";
import type { RunSummary } from "../api/types";
import { displayWorkspace, durationBetween, formatDate, humanize } from "../lib/format";
import { StatusSignal } from "./Primitives";

export function RunLedgerRecord({ run }: { run: RunSummary }) {
  const fileCount = run.changed_files?.length;
  const checks = run.verifier_status ? humanize(run.verifier_status) : "Not reported";
  const review = run.reviewer_status ? humanize(run.reviewer_status) : "Not reported";
  return <a className="ledger-record" href={`#/runs/${encodeURIComponent(run.run_id)}`} aria-label={`${run.original_task}. ${humanize(run.status)}. Repository ${displayWorkspace(run.workspace)}. Checks: ${checks}. Review: ${review}.`}>
    <span className="ledger-record-identity"><code title={run.run_id}>{run.run_id.slice(0, 8)}</code><span>{humanize(run.workflow)}</span></span>
    <span className="ledger-record-content">
      <strong>{run.original_task}</strong>
      <span className="ledger-record-context"><span>{displayWorkspace(run.workspace)}</span><span>{fileCount === undefined ? "Files not reported" : `${fileCount} ${fileCount === 1 ? "file" : "files"}`}</span>{run.completed_at && <span>{durationBetween(run.created_at, run.completed_at)}</span>}</span>
      {run.failure_reason && <span className="ledger-record-failure" title={run.failure_reason}>{run.failure_reason}</span>}
    </span>
    <span className="ledger-record-proof"><span>Checks {run.verifier_status ? <StatusSignal status={run.verifier_status} label={checks} /> : <span>{checks}</span>}</span><span>Review {run.reviewer_status ? <StatusSignal status={run.reviewer_status} label={review} /> : <span>{review}</span>}</span></span>
    <span className="ledger-record-outcome"><StatusSignal status={run.status} /><time dateTime={run.created_at} title={`Created: ${run.created_at}. Last update: ${run.updated_at}.`}>{formatDate(run.created_at)}</time></span>
    <ArrowUpRight className="ledger-record-open" size={18} aria-hidden="true" />
  </a>;
}
