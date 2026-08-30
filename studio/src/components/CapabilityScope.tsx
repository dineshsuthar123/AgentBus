import { LockKeyhole, ShieldCheck } from "lucide-react";
import type { ToolCapability } from "../api/types";

export function CapabilityScope({ capabilities, approvalRequired = false }: { capabilities: ToolCapability[]; approvalRequired?: boolean }) {
  const visible = capabilities.slice(0, 6);
  return (
    <div className={`capability-scope ${approvalRequired ? "requires-approval" : ""}`}>
      <div className="capability-core" title={approvalRequired ? "Exact approval required" : "Policy validated"}>
        {approvalRequired ? <LockKeyhole size={17} /> : <ShieldCheck size={17} />}
      </div>
      <div className="capability-orbit">
        {visible.map((capability, index) => (
          <div className="capability-chip" style={{ "--cap-index": index } as React.CSSProperties} key={`${capability.name}-${index}`}>
            <code>{capability.name}</code>
            <small>{scopeLabel(capability)}</small>
          </div>
        ))}
      </div>
      {capabilities.length > visible.length && <span className="capability-more">+{capabilities.length - visible.length}</span>}
    </div>
  );
}

function scopeLabel(capability: ToolCapability): string {
  const scope = capability.scope;
  if (scope?.executables?.length) return scope.executables.join(", ");
  if (scope?.affected_paths?.length) return `${scope.affected_paths.length} path${scope.affected_paths.length === 1 ? "" : "s"}`;
  if (scope?.working_directories?.length) return "workspace";
  if (scope?.network_allowed) return "bounded network";
  return "current scope";
}
