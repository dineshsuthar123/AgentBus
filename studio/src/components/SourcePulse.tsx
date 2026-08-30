import { FileCheck2, FileCode2, FileLock2, Wand2 } from "lucide-react";
import type { ChangeSummary } from "../api/types";

export function SourcePulse({ changes }: { changes: ChangeSummary[] }) {
  if (!changes.length) return <p className="quiet-copy">Repository changes appear here as AgentBus observes them.</p>;
  return (
    <div className="source-pulse">
      {changes.map((change) => {
        const Icon = change.generated ? Wand2 : change.classification.includes("excluded") ? FileLock2 : change.status.toLowerCase().includes("mod") ? FileCode2 : FileCheck2;
        return (
          <div className="source-row" key={change.path}>
            <span className={`source-icon state-${change.generated ? "generated" : change.status.toLowerCase()}`}><Icon size={15} /></span>
            <code title={change.path}>{change.path}</code>
            <span className="source-state">{change.generated ? "generated" : change.classification}</span>
            <span className="source-delta">{change.binary ? "binary" : `+${change.additions ?? 0} −${change.deletions ?? 0}`}</span>
          </div>
        );
      })}
    </div>
  );
}
