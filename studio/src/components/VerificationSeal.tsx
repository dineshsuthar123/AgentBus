import { CheckCheck, ShieldX } from "lucide-react";
import { humanize } from "../lib/format";

export function VerificationSeal({ status, summary, tests }: { status?: string | null; summary?: string; tests?: string }) {
  const passed = ["passed", "succeeded", "approved", "true"].includes(String(status).toLowerCase());
  return (
    <div className={`verification-seal ${passed ? "seal-passed" : "seal-failed"}`}>
      <div className="seal-ring">{passed ? <CheckCheck size={27} /> : <ShieldX size={27} />}</div>
      <div><span>Verifier evidence</span><strong>{passed ? "Verified" : status ? humanize(status) : "Awaiting verification"}</strong><p>{tests ?? summary ?? (passed ? "Repository checks reached a valid terminal result." : "No successful verifier result is available yet.")}</p></div>
    </div>
  );
}
