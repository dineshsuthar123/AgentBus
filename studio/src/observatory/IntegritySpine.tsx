import { Activity, Fingerprint, Network, PlaySquare, RadioTower, ShieldCheck } from "lucide-react";
import type { RunBundle } from "../api/runBundle";
import type { StreamStatus } from "../api/sse";
import { shortId } from "../lib/format";
import type { InspectorView } from "./selection";

export type PresentationMode = "LIVE" | "HISTORICAL" | "REPLAY";

export function IntegritySpine({ bundle, mode, onSelect, stream }: {
  bundle: RunBundle;
  mode: PresentationMode;
  onSelect: (view: InspectorView) => void;
  stream: StreamStatus;
}) {
  const replay = bundle.replays?.replays[0];
  const processInvocations = bundle.invocations?.invocations.filter((invocation) => invocation.capabilities.some((capability) => capability.name === "process.execute" || capability.name === "test.execute")).length;
  const networkInvocations = bundle.invocations?.invocations.filter((invocation) => invocation.capabilities.some((capability) => capability.name === "process.network")).length;
  return <aside className={`integrity-spine mode-${mode.toLowerCase()}`} aria-label="Integrity spine">
    <header><ShieldCheck size={15} /><span>Integrity</span></header>
    <SpineMetric icon={<Fingerprint size={12} />} label="Trace" value={bundle.trace?.status ?? "unsealed"} tone={bundle.trace?.status === "sealed" ? "success" : "muted"} onClick={() => onSelect("evidence")} />
    <SpineMetric label="Provenance" value={shortId(bundle.provenance?.integrity_root, 4)} tone={bundle.provenance ? "success" : "muted"} onClick={() => onSelect("evidence")} />
    <SpineMetric icon={<Activity size={12} />} label="Mode" value={mode} tone={mode === "LIVE" ? "active" : "replay"} onClick={() => onSelect("evidence")} />
    <SpineMetric icon={<RadioTower size={12} />} label="Provider" value={String(replay?.provider_calls ?? bundle.provenance?.provider_routes.length ?? "--")} onClick={() => onSelect("evidence")} />
    <SpineMetric icon={<Network size={12} />} label="Network" value={String(replay?.network_calls ?? networkInvocations ?? "--")} onClick={() => onSelect("runtime")} />
    <SpineMetric icon={<PlaySquare size={12} />} label="Process" value={String(replay?.process_dispatches ?? processInvocations ?? "--")} onClick={() => onSelect("evidence")} />
    {replay && <SpineMetric label="Captured" value={String(replay.captured_tool_results_reused ?? 0)} tone="replay" onClick={() => onSelect("evidence")} />}
    <SpineMetric label="Replay" value={bundle.replayability?.level ?? "unclassified"} tone="replay" onClick={() => onSelect("evidence")} />
    <footer className={`spine-stream phase-${stream.phase}`}><i /><span>{stream.phase}</span><code>{stream.cursor || "--"}</code></footer>
  </aside>;
}

function SpineMetric({ icon, label, value, tone = "muted", onClick }: { icon?: React.ReactNode; label: string; value: string; tone?: "active" | "muted" | "replay" | "success"; onClick: () => void }) {
  return <button className={`spine-metric tone-${tone}`} type="button" onClick={onClick}>{icon}<span>{label}</span><strong>{value}</strong></button>;
}
