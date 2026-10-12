import { ArrowRight, Ban, Clock3, LockKeyhole } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import type { ApprovalSummary } from "../api/types";
import { displayCommand, displayWorkspace } from "../lib/format";
import { CapabilityScope } from "./CapabilityScope";
import { TraceIdentity } from "./Primitives";

export function ApprovalGate({ approval, onDecision, busy = false }: {
  approval: ApprovalSummary;
  onDecision: (decision: "approve" | "reject", reason?: string) => Promise<void>;
  busy?: boolean;
}) {
  const [reason, setReason] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  useLayoutEffect(() => {
    heading.current?.focus();
  }, [approval.approval_id, approval.revision]);
  return (
    <section className="approval-gate" aria-labelledby={`approval-title-${approval.approval_id}`} aria-busy={busy}>
      <div className="gate-rule"><span /><strong><LockKeyhole size={14} /> Approval gate</strong><span /></div>
      <div className="gate-body">
        <div className="gate-main">
          <p className="eyebrow">Execution paused by policy</p>
          <h2 id={`approval-title-${approval.approval_id}`} ref={heading} tabIndex={-1}>{approval.requested_action}</h2>
          <dl className="gate-command">
            <div><dt>Tool</dt><dd><code>{approval.tool_name ?? "managed tool"}</code></dd></div>
            <div><dt>Executable</dt><dd><code>{approval.executable ?? approval.command?.[0] ?? "bounded action"}</code></dd></div>
            <div className="gate-command-exact"><dt>Command summary</dt><dd><code>{displayCommand(approval.command)}</code></dd></div>
          </dl>
          <dl className="technical-grid">
            <div><dt>Workspace scope</dt><dd title="Canonical path retained by Syndra">{displayWorkspace(approval.working_directory)}</dd></div>
            <div><dt>Policy rule</dt><dd><code>{approval.policy_rule ?? approval.risk_category}</code></dd></div>
            <div><dt>Reason</dt><dd>{approval.reason ?? "Exact human authorization is required."}</dd></div>
            <div><dt>Revision</dt><dd>{approval.revision ?? 1}</dd></div>
            <div><dt>Task</dt><dd><code>{approval.task_id}</code></dd></div>
            <div><dt>Approval state</dt><dd>{approval.state}</dd></div>
          </dl>
          {approval.arguments_summary?.length ? <div className="gate-arguments"><span>Bounded argument summary</span>{approval.arguments_summary.map((item) => <code key={item}>{item}</code>)}</div> : null}
          <details className="approval-request-details"><summary>Request scope and resource limits</summary>
            <p>Bounded/redacted API summaries are not unrestricted tool arguments. Authorization binds to this persisted request and revision, not the displayed command summary, and grants no permanent permission.</p>
            <dl className="technical-grid">
              <div><dt>Run</dt><dd><code>{approval.run_id}</code></dd></div>
              <div><dt>Working directory</dt><dd><code>{approval.working_directory ?? "Not persisted"}</code></dd></div>
              <div><dt>Network destination</dt><dd><code>{approval.network_destination ?? "Not reported; inspect the capability scopes"}</code></dd></div>
              <div><dt>Tool version</dt><dd>{approval.tool_version ? `${approval.tool_version.major}.${approval.tool_version.minor ?? 0}.${approval.tool_version.patch ?? 0}` : "Not persisted"}</dd></div>
              <div><dt>Affected paths</dt><dd>{approval.affected_paths?.length ? <ul>{approval.affected_paths.map((path, index) => <li key={index}><code>{path}</code></li>)}</ul> : "No affected path list persisted"}</dd></div>
            </dl>
            <h3>Requested resource budgets</h3>
            <p>These are requested limits. Platform support and observed enforcement are recorded separately with the invocation evidence.</p>
            {approval.resource_budget ? <dl className="technical-grid">{Object.entries(approval.resource_budget).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{value == null ? "Not requested" : String(value)}</dd></div>)}</dl> : <p>No managed-tool resource budget was persisted.</p>}
            {approval.proposed_constraints?.length ? <><h3>Proposed constraints</h3><CapabilityScope capabilities={approval.proposed_constraints} approvalRequired /></> : <p>No additional proposed constraints were persisted.</p>}
          </details>
          <TraceIdentity label="Approval" value={approval.approval_id} />
        </div>
        <div className="gate-scope">
          <p className="section-label">Capability envelope</p>
          <CapabilityScope capabilities={approval.capabilities ?? []} approvalRequired />
          {approval.expires_at && <p className="expiry"><Clock3 size={13} /> Expires {new Date(approval.expires_at).toLocaleTimeString()}</p>}
        </div>
      </div>
      <div className="gate-actions">
        <label><span>Decision note <small>optional</small></span><input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Why are you approving or rejecting?" maxLength={2_000} /></label>
        <div>
          <button className="button button-danger" type="button" disabled={busy} title={busy ? "Waiting for server confirmation" : undefined} onClick={() => void onDecision("reject", reason)}><Ban size={15} /> {busy ? "Decision pending" : "Reject"}</button>
          <button className="button button-approval" type="button" disabled={busy} title={busy ? "Waiting for server confirmation" : undefined} onClick={() => void onDecision("approve", reason)}>{busy ? "Waiting for Syndra" : "Approve & continue"} <ArrowRight size={15} /></button>
        </div>
      </div>
      <p className="gate-authority" aria-live="polite">{busy ? "Decision submitted. The gate remains closed until Syndra confirms it." : "Server-authoritative gate. No execution continues before confirmation."}</p>
    </section>
  );
}
