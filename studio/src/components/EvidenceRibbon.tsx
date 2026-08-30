import { Activity, Network, PlaySquare, RadioTower, ShieldCheck } from "lucide-react";
import type { ProvenanceResponse, ReplaySessionResponse, RunReplayabilityResponse, TraceResponse } from "../api/types";
import { shortId } from "../lib/format";

export function EvidenceRibbon({ trace, provenance, replayability, replay, streamConnected }: {
  trace?: TraceResponse;
  provenance?: ProvenanceResponse;
  replayability?: RunReplayabilityResponse;
  replay?: ReplaySessionResponse;
  streamConnected?: boolean;
}) {
  return (
    <aside className="evidence-ribbon" aria-label="Evidence ribbon">
      <div className="ribbon-title"><ShieldCheck size={15} /><span>Evidence</span></div>
      <RibbonItem label="Trace" value={trace ? trace.status : "unsealed"} accent="trace" />
      <RibbonItem label="Objects" value={String(provenance?.integrity_object_count ?? 0)} />
      <RibbonItem label="Provenance" value={shortId(provenance?.integrity_root, 6)} mono />
      <RibbonItem label="Replay" value={replayability?.level ?? "not classified"} accent="replay" />
      <RibbonItem icon={<RadioTower size={12} />} label="Provider" value={String(replay?.provider_calls ?? "—")} />
      <RibbonItem icon={<Network size={12} />} label="Network" value={String(replay?.network_calls ?? "—")} />
      <RibbonItem icon={<PlaySquare size={12} />} label="Process" value={String(replay?.process_dispatches ?? "—")} />
      <div className={`ribbon-stream ${streamConnected ? "online" : "offline"}`}><Activity size={13} /><span>{streamConnected ? "Live stream" : "Snapshot"}</span></div>
    </aside>
  );
}

function RibbonItem({ label, value, mono = false, accent, icon }: { label: string; value: string; mono?: boolean; accent?: string; icon?: React.ReactNode }) {
  return <div className={`ribbon-item ${accent ? `accent-${accent}` : ""}`}>{icon}<span>{label}</span><strong className={mono ? "mono" : ""}>{value}</strong></div>;
}
