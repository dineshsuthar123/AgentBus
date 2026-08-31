import { LockKeyhole, ShieldCheck } from "lucide-react";
import type { ToolCapability } from "../api/types";

export function CapabilityScope({ capabilities, approvalRequired = false }: { capabilities: ToolCapability[]; approvalRequired?: boolean }) {
  return (
    <section className={`capability-envelope ${approvalRequired ? "requires-approval" : "is-authorized"}`} aria-label="Capability envelope">
      <header>
        <span>{approvalRequired ? <LockKeyhole size={14} /> : <ShieldCheck size={14} />}</span>
        <div><strong>Capability envelope</strong><small>{approvalRequired ? "Exact approval required" : "Policy authorized"}</small></div>
      </header>
      {capabilities.length ? <ul>
        {capabilities.map((capability, index) => (
          <li key={`${capability.name}-${index}`}>
            <span className="envelope-rail" aria-hidden="true" />
            <code>{capability.name}</code>
            <small>{scopeLabel(capability)}</small>
          </li>
        ))}
      </ul> : <p>No capability descriptor was persisted for this action.</p>}
      <footer><span>Boundary</span><code>{boundaryLabel(capabilities)}</code></footer>
    </section>
  );
}

function scopeLabel(capability: ToolCapability): string {
  const scope = capability.scope;
  if (scope?.executables?.length) return `executable: ${scope.executables.join(", ")}`;
  if (scope?.affected_paths?.length) return `paths: ${scope.affected_paths.join(", ")}`;
  if (scope?.patterns?.length) return `patterns: ${scope.patterns.join(", ")}`;
  if (scope?.roots?.length) return `roots: ${scope.roots.join(", ")}`;
  if (scope?.working_directories?.length) return `cwd: ${scope.working_directories.join(", ")}`;
  if (scope?.network_destinations?.length) return `destinations: ${scope.network_destinations.join(", ")}`;
  if (scope?.network_allowed) return "bounded network access";
  return "current repository scope";
}

function boundaryLabel(capabilities: ToolCapability[]): string {
  if (capabilities.some((capability) => capability.scope?.network_allowed)) return "workspace + bounded network";
  if (capabilities.some((capability) => capability.name.startsWith("process.") || capability.name === "test.execute")) return "workspace process";
  return "target repository";
}
