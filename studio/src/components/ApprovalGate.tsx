import { ArrowRight, Ban, Clock3, LockKeyhole } from "lucide-react";
import { useState } from "react";
import type { ApprovalSummary } from "../api/types";
import { CapabilityScope } from "./CapabilityScope";
import { TraceIdentity } from "./Primitives";

export function ApprovalGate({ approval, onDecision, busy = false }: {
  approval: ApprovalSummary;
  onDecision: (decision: "approve" | "reject", reason?: string) => Promise<void>;
  busy?: boolean;
}) {
  const [reason, setReason] = useState("");
  return (
    <section className="approval-gate" aria-label="Approval gate">
      <div className="gate-rule"><span /><strong><LockKeyhole size={14} /> Approval gate</strong><span /></div>
      <div className="gate-body">
        <div className="gate-main">
          <p className="eyebrow">Execution paused by policy</p>
          <h2>{approval.tool_name ?? approval.requested_action}</h2>
          <div className="gate-command">
            <span>Executable</span>
            <code>{approval.executable ?? approval.command?.[0] ?? "bounded action"}</code>
          </div>
          <dl className="technical-grid">
            <div><dt>Working directory</dt><dd>{approval.working_directory ?? "Current workspace"}</dd></div>
            <div><dt>Policy rule</dt><dd><code>{approval.policy_rule ?? approval.risk_category}</code></dd></div>
            <div><dt>Reason</dt><dd>{approval.reason ?? "Exact human authorization is required."}</dd></div>
            <div><dt>Revision</dt><dd>{approval.revision ?? 1}</dd></div>
          </dl>
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
          <button className="button button-danger" type="button" disabled={busy} onClick={() => void onDecision("reject", reason)}><Ban size={15} /> Reject</button>
          <button className="button button-approval" type="button" disabled={busy} onClick={() => void onDecision("approve", reason)}>Approve &amp; continue <ArrowRight size={15} /></button>
        </div>
      </div>
    </section>
  );
}
