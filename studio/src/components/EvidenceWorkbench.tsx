import { useEffect, useRef, useState } from "react";
import type { StudioClient } from "../api/client";
import type { RunBundle } from "../api/runBundle";
import type { ComparisonResponse, ReplaySessionResponse, TraceArchiveImportResponse } from "../api/types";
import { ReplaySession } from "./ReplaySession";
import { ErrorPanel } from "./Primitives";

const MAX_IMPORT_BYTES = 10 * 1024 * 1024;

export function EvidenceWorkbench({ bundle, client }: { bundle: RunBundle; client: StudioClient }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [archive, setArchive] = useState<File>();
  const [imported, setImported] = useState<TraceArchiveImportResponse>();
  const [other, setOther] = useState("");
  const [comparison, setComparison] = useState<ComparisonResponse>();
  const [checkpoint, setCheckpoint] = useState("");
  const [session, setSession] = useState<ReplaySessionResponse>();
  const active = useRef(true);
  const operation = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  async function act(action: () => Promise<void>) {
    if (operation.current) return;
    operation.current = true; setBusy(true); setError(undefined); setNotice(undefined);
    try { await action(); } catch (reason) { if (active.current) setError(reason instanceof Error ? reason.message : "Evidence operation failed; no successful outcome is implied."); }
    finally { operation.current = false; if (active.current) setBusy(false); }
  }
  const trace = bundle.trace;
  const terminal = ["succeeded", "completed", "failed", "rejected", "cancelled"].includes(bundle.run.status);
  const checkpoints = trace?.checkpoints ?? [];
  return <section className="evidence-workbench" aria-label="Evidence management">
    <h3>Evidence management</h3><p>Validated local archives and captured offline replay. Importing evidence does not execute its tools, approve a request or resume a source run. No live provider consent is sent by these controls.</p>
    {error && <ErrorPanel message={error} />}{notice && <p role="status">{notice}</p>}
    <div className="facility-actions">
      <button disabled={busy || !trace || !terminal} onClick={() => void act(async () => { const value = await client.exportTrace(trace!.trace_id); if (active.current) { await downloadArchive(value.archive_base64, value.archive_sha256, `syndra-trace-${safeId(bundle.run.run_id)}.zip`); setNotice(`Exported validated trace ${value.trace_id}; source content excluded.`); } })}>Export trace archive</button>
      <button disabled={busy || !trace || !terminal} onClick={() => void act(async () => { const value = await client.captureFixture(bundle.run.run_id); if (active.current) { await downloadArchive(value.archive_base64, value.archive_sha256, `syndra-fixture-${safeId(bundle.run.run_id)}.zip`); setNotice(`Regression fixture captured; assertions ${value.assertions_validated ? "validated" : "not confirmed"}. No replay was started. ${value.source_warning ?? ""} ${value.license_warning ?? ""}`); } })}>Capture regression fixture</button>
    </div>
    <form onSubmit={(event) => { event.preventDefault(); void act(async () => {
      if (!archive || archive.size > MAX_IMPORT_BYTES) throw new Error("Choose an archive no larger than 10 MiB. No import has occurred.");
      const bytes = new Uint8Array(await archive.arrayBuffer());
      if (!active.current) return;
      const result = await client.importTrace(encodeBase64(bytes));
      if (active.current) { setImported(result); setOther(result.trace_id); setNotice(`Archive accepted: ${result.trace_id}. Objects ${result.objects_imported ? "imported" : "already present"}; replay not started.`); }
    }); }}>
      <label>Import trace archive (10 MiB maximum)<input type="file" accept=".zip,application/zip" disabled={busy} onChange={(event) => { setArchive(event.target.files?.[0]); setImported(undefined); }} /></label>
      <p>Source-content archives are refused. Integrity, compatibility and traversal checks remain authoritative on the daemon. Rejected archives leave the source run unchanged.</p><button disabled={busy || !archive || archive.size > MAX_IMPORT_BYTES}>Validate and import archive</button>
      {archive && archive.size > MAX_IMPORT_BYTES && <p role="alert">Archive exceeds the 10 MiB browser limit. Use the existing bounded CLI import for supported larger archives.</p>}
    </form>
    {imported && <p>Imported run <code>{imported.run_id}</code>; provenance <code>{imported.provenance_root}</code>. Inspection-only evidence, not proof of a new execution.</p>}
    <form onSubmit={(event) => { event.preventDefault(); void act(async () => { const value = await client.compare(trace!.trace_id, other.trim()); if (active.current) setComparison(value); }); }}>
      <label>Compare with persisted run, trace or replay ID<input value={other} maxLength={128} onChange={(event) => setOther(event.target.value)} /></label><button disabled={busy || !trace || !other.trim()}>Compare evidence</button>
    </form>
    {comparison && <div><h4>Comparison {comparison.comparison_id}</h4><p>{comparison.left_status} versus {comparison.right_status}. {comparison.summary.unchanged_spans} unchanged, {comparison.summary.changed_spans} changed, {comparison.summary.added_spans} added and {comparison.summary.removed_spans} removed spans.</p><ul>{comparison.spans.flatMap((span) => span.differences ?? []).map((difference, position) => <li key={position}>{difference.category}: {difference.summary}</li>)}</ul>{comparison.truncated && <p>Bounded comparison page; more spans remain.</p>}<div className="facility-actions"><button disabled={busy || !comparison.after} onClick={() => void act(async () => { const value = await client.comparison(comparison.comparison_id, Math.max(0, (comparison.after ?? 0) - 100)); if (active.current) setComparison(value); })}>Previous comparison spans</button><button disabled={busy || !comparison.truncated || comparison.next_after === undefined} onClick={() => void act(async () => { const value = await client.comparison(comparison.comparison_id, comparison.next_after!); if (active.current) setComparison(value); })}>Next comparison spans</button></div></div>}
    <label>Persisted checkpoint<select value={checkpoint} onChange={(event) => setCheckpoint(event.target.value)}><option value="">Choose a replayable checkpoint</option>{checkpoints.map((value) => <option value={value.checkpoint_id} key={value.checkpoint_id} disabled={!value.replayable}>{value.sequence}: {value.label}{value.replayable ? "" : " (not replayable)"}</option>)}</select></label>
    {!checkpoints.length && <p>No checkpoint records are available in this snapshot.</p>}{trace?.checkpoints_truncated && <p>Checkpoint list reached the backend's bounded limit.</p>}
    <button disabled={busy || !checkpoints.some((value) => value.checkpoint_id === checkpoint && value.replayable) || !terminal || !bundle.replayability?.replayable_offline} onClick={() => void act(async () => { const value = await client.createReplay(bundle.run.run_id, { mode: "offline", from_checkpoint_id: checkpoint, fork: true, live_provider_consent: false }); if (active.current) setSession(value); })}>Fork captured replay from checkpoint</button>
    <p>A captured fork uses persisted inputs and results; it is not a new implementation run. Modified-input/live verification forks remain available through the explicit CLI/native controls, not an unrestricted browser JSON editor.</p>
    {session && <ReplaySession key={session.replay_id} client={client} initial={session} />}
  </section>;
}

function safeId(value: string) { return value.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 128); }

function encodeBase64(bytes: Uint8Array) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  return btoa(binary);
}

async function downloadArchive(encoded: string, expectedHash: string, name: string) {
  if (encoded.length > 64 * 1024 * 1024) throw new Error("Archive is too large for browser export; use the existing CLI.");
  const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (byte) => byte.toString(16).padStart(2, "0")).join("");
  if (digest !== expectedHash) throw new Error("Archive hash mismatch. No download was saved.");
  const url = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
  const link = document.createElement("a"); link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
