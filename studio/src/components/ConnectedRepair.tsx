import { useEffect, useRef, useState } from "react";
import type { StudioClient } from "../api/client";
import type { ConnectedPlan, ConnectedPreview, ConnectionStatus, RepairCase } from "../api/types";
import { ErrorPanel } from "./Primitives";

export function ConnectedRepair({ client, current }: { client: StudioClient; current: RepairCase }) {
  const caseId = current.summary.case_id;
  const [connections, setConnections] = useState<ConnectionStatus[]>([]);
  const [connectionId, setConnectionId] = useState("");
  const [preview, setPreview] = useState<ConnectedPreview>();
  const [selected, setSelected] = useState<string[]>([]);
  const [plans, setPlans] = useState<ConnectedPlan[]>([]);
  const [result, setResult] = useState<ConnectedPlan>();
  const [cap, setCap] = useState(1);
  const [authority, setAuthority] = useState<"application" | "stripe">("application");
  const [candidates, setCandidates] = useState<string[]>();
  const [reviewed, setReviewed] = useState(false);
  const [trusted, setTrusted] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [clock, setClock] = useState(() => Date.now());
  const active = useRef(false);
  const alive = useRef(true);
  const reviewHeading = useRef<HTMLHeadingElement>(null);
  const focusReview = useRef(false);
  useEffect(() => {
    alive.current = true;
    const abort = new AbortController();
    async function load() {
      try {
        const [targets, saved, history] = await Promise.all([client.repairConnections({ signal: abort.signal, force: true }), client.connectedPreview(caseId, 0, { signal: abort.signal, force: true }), client.connectedPlans(caseId, { signal: abort.signal, force: true })]);
        if (abort.signal.aborted) return;
        setConnections(targets.connections); setPreview(saved.preview ?? undefined); setConnectionId(saved.preview?.connection_id ?? ""); setPlans(history.results); setResult(history.results[0]);
      } catch { if (!abort.signal.aborted) setError("Connected history is unavailable. No correction was dispatched. Refresh this case to reconnect."); }
    }
    void load(); return () => { alive.current = false; abort.abort(); };
  }, [client, caseId]);
  useEffect(() => {
    if (!result) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [result]);
  useEffect(() => {
    if (!focusReview.current) return;
    focusReview.current = false;
    reviewHeading.current?.focus({ preventScroll: true });
    reviewHeading.current?.scrollIntoView?.({ block: "nearest" });
  }, [result]);
  async function act(label: string, operation: () => Promise<void>) {
    if (active.current) return;
    active.current = true; setBusy(label); setError("");
    try { await operation(); } catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : "Outcome is unconfirmed. Read the saved plan before continuing."); }
    finally { active.current = false; if (alive.current) setBusy(""); }
  }
  function invalidate() { setPreview(undefined); setSelected([]); setResult(undefined); setReviewed(false); }
  function save(value: ConnectedPlan) { if (!alive.current) return; focusReview.current = true; setResult(value); setPlans((old) => [value, ...old.filter((item) => item.plan.plan_id !== value.plan.plan_id)]); setReviewed(false); }
  const connection = connections.find((item) => item.manifest.connection_id === connectionId);
  async function refresh() {
    const value = await client.refreshConnectedRepair(caseId, { connection_id: connectionId, policy: { incident_start: current.snapshot.incident_start, incident_end: current.snapshot.incident_end, maximum_writes: cap, payment_authority: authority }, ...(candidates ? { candidate_ids: candidates } : {}) });
    if (alive.current) { setPreview(value.preview ?? undefined); setSelected([]); setResult(undefined); setReviewed(false); }
  }
  const visible = preview?.decisions.filter((item) => item.purchase_id.toLowerCase().includes(search.toLowerCase())) ?? [];
  const expired = result ? Date.parse(result.plan.expires_at) <= clock : false;
  return <section className="repair-connected" aria-label="Connected application repair"><header><p className="eyebrow">Authoritative application connection</p><h2>From candidate records to a real correction.</h2><p>An import identifies candidates only. Current authority, exact approval and a conditional domain operation determine what changes.</p></header>
    <fieldset disabled={!!busy}><legend>Target and reviewed purchase-to-access policy</legend><label>Application connection<select value={connectionId} onChange={(event) => { invalidate(); setConnectionId(event.target.value); }}><option value="">Choose a configured application</option>{connections.map((item) => <option key={item.manifest.connection_id} value={item.manifest.connection_id}>{item.manifest.connection_id} / {item.manifest.identity.environment}</option>)}</select></label><label>Payment authority<select value={authority} onChange={(event) => { invalidate(); setAuthority(event.target.value as "application" | "stripe"); }}><option value="application">Application-owned payment aggregate</option><option value="stripe">Stripe evidence (requires coordinated refund path)</option></select></label><label>Maximum approved writes<input type="number" min={1} max={200} value={cap} onChange={(event) => { invalidate(); setCap(Math.min(200, Math.max(1, Number(event.target.value) || 1))); }} /></label><p>Default: one-record canary. Restore the original purchased period, never a fresh period. Partial refunds and expired access require investigation. Incident: {new Date(current.snapshot.incident_start).toLocaleString()} to {new Date(current.snapshot.incident_end).toLocaleString()}, end excluded.</p><label>Candidate purchase IDs CSV<input type="file" accept=".csv,text/csv" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (!file) return; void act("Reading candidate IDs", async () => { if (file.size > 850_000) throw new Error("Use a candidate CSV below 850 KB."); const lines = (await file.text()).replace(/^\uFEFF/, "").trim().split(/\r?\n/); if (lines.shift() !== "purchase_id" || !lines.length || lines.length > 10000 || lines.some((line) => !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$/.test(line)) || new Set(lines).size !== lines.length) throw new Error("Use one purchase_id column and 1-10000 unique pseudonymous IDs, not customer bodies or credentials."); if (alive.current) { invalidate(); setCandidates(lines); } }); }} /></label><span>{candidates ? `${candidates.length} CSV candidates` : `${current.snapshot.purchases.length} imported candidates`}; neither authorizes writes.</span></fieldset>
    {connection ? <div className="repair-target"><strong>{connection.manifest.identity.application} / {connection.manifest.identity.tenant}</strong><span className={`repair-environment env-${connection.manifest.identity.environment}`}>{connection.manifest.identity.environment}</span><code>{connection.manifest.destination}</code><p>{connection.reason}</p><button disabled={!!busy} onClick={() => void act("Checking target identity", async () => { const value = await client.checkRepairConnection(connectionId); if (alive.current) setConnections((old) => old.map((item) => item.manifest.connection_id === connectionId ? value.connection : item)); })}>Check target identity</button></div> : <p>Configure a reviewed destination in Connections. Imports cannot supply credentials or network destinations.</p>}
    <button disabled={!!busy || !connectionId} onClick={() => void act("Rehydrating authoritative records", refresh)}>Refresh authoritative evidence</button>
    {preview && <><div className="repair-counts"><div><strong>{preview.examined}</strong><span>Examined</span></div>{Object.entries(preview.counts).map(([key, value]) => <div key={key}><strong>{value}</strong><span>{key.replaceAll("_", " ")}</span></div>)}</div><label>Search decision page<input type="search" maxLength={80} value={search} onChange={(event) => setSearch(event.target.value)} /></label>
      <ul className="repair-connected-records" aria-label="Authoritative candidate decisions" tabIndex={0}>{visible.map((item) => <li key={item.purchase_id}><label><input type="checkbox" aria-label={`Select ${item.purchase_id}: ${item.disposition.replaceAll("_", " ")}`} checked={selected.includes(item.purchase_id)} disabled={!!busy || item.disposition !== "eligible" || (!selected.includes(item.purchase_id) && selected.length >= cap)} onChange={(event) => { setReviewed(false); setResult(undefined); setSelected((old) => event.target.checked ? [...old, item.purchase_id] : old.filter((id) => id !== item.purchase_id)); }} /><strong>{item.purchase_id}</strong><span>{item.disposition.replaceAll("_", " ")}</span></label><p>{item.reason}</p>{item.uncertainty && <small>{item.uncertainty}</small>}<details><summary>Evidence and preconditions</summary><p>Account {mask(item.state?.account_id)} / revision {item.state?.revision ?? "unknown"} / feature {item.state?.feature ?? "unknown"}</p><p>References: {item.state?.evidence_ids.join(", ") ?? "None"}</p></details></li>)}</ul><div className="facility-actions"><button disabled={!!busy || preview.offset === 0} onClick={() => void act("Reading prior decisions", async () => { const value = await client.connectedPreview(caseId, Math.max(0, preview.offset - 50), { force: true }); if (alive.current) setPreview(value.preview ?? undefined); })}>Previous decisions</button><button disabled={!!busy || preview.offset + 50 >= preview.examined} onClick={() => void act("Reading next decisions", async () => { const value = await client.connectedPreview(caseId, preview.offset + 50, { force: true }); if (alive.current) setPreview(value.preview ?? undefined); })}>Next decisions</button><span>{selected.length} selected / {cap} write cap</span></div><label className="repair-confirm"><input type="checkbox" checked={trusted} disabled={!!busy} onChange={(event) => setTrusted(event.target.checked)} />I trust this workspace to request the selected conditional application corrections.</label><button disabled={!!busy || !trusted || !selected.length || !connection?.write_ready} onClick={() => void act("Preparing exact correction plan", async () => { const value = await client.createConnectedPlan(caseId, { scan_id: preview.scan_id, purchase_ids: selected, workspace_trusted: trusted }); save(value.result); })}>Review {selected.length === 1 ? "one-record canary" : `${selected.length}-record plan`}</button>{!connection?.write_ready && <p>Apply unavailable: {connection?.reason ?? "Validate the connection first."}</p>}</>}
    {result && <section className="repair-plan-review" aria-label="Exact connected correction plan"><h3 ref={reviewHeading} tabIndex={-1}>{result.plan.maximum_writes === 1 ? "One account. One exact correction." : `${result.plan.maximum_writes} exact conditional corrections.`}</h3><p>{result.plan.identity.application} / {result.plan.identity.tenant} / <strong>{result.plan.identity.environment}</strong></p><p>Policy {result.plan.operations[0]?.policy.version}. Expires {new Date(result.plan.expires_at).toLocaleString()} ({Intl.DateTimeFormat().resolvedOptions().timeZone}).</p><code className="repair-plan-digest">{result.plan.digest}</code><ul>{result.plan.operations.map((operation) => <li key={operation.operation_id}><strong>{operation.purchase_id}</strong>: set access to true for {mask(operation.effect.account_id)}, feature {operation.effect.feature}, {new Date(operation.effect.period_start).toLocaleString()} to {new Date(operation.effect.period_end).toLocaleString()}; expected revision {operation.expected_revision}. Side effect: idempotent access-projection outbox event. Verification: normal application authorization path.</li>)}</ul>
      {!result.approved && <><label className="repair-confirm"><input type="checkbox" checked={reviewed} disabled={!!busy || expired || result.stopped} onChange={(event) => setReviewed(event.target.checked)} />I reviewed this exact target, policy, selected effects and write limit.</label><button disabled={!!busy || !reviewed || expired || result.stopped} onClick={() => void act("Approving exact plan", async () => { const value = await client.approveConnectedPlan(result.plan.plan_id, result.plan.digest); save(value.result); })}>Approve this exact plan</button></>}
      {expired && <p>Approval expired. No new corrections may dispatch. Saved receipts can still be reconciled.</p>}
      <div className="facility-actions"><button disabled={!!busy || !result.approved || result.stopped || expired || result.status === "completed"} onClick={() => void act("Executing approved conditional operations", async () => { const value = await client.executeConnectedPlan(result.plan.plan_id); save(value.result); })}>Execute approved {result.plan.maximum_writes === 1 ? "canary" : "batch"}</button><button disabled={!!busy} onClick={() => void act("Reconciling saved receipts and access", async () => { const value = await client.reconcileConnectedPlan(result.plan.plan_id); save(value.result); })}>Reconcile saved operations</button><button disabled={result.stopped && result.status === "stopped"} onClick={() => { void client.stopConnectedPlan(result.plan.plan_id).then((value) => save(value.result)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Stop is unconfirmed; dispatched operations may still commit.")); }}>Emergency stop</button></div>
      <p role="status">{result.status.replaceAll("_", " ")}. Stop prevents new dispatch, not an undo of committed changes.</p><div className="repair-counts">{Object.entries(result.counts).map(([key, value]) => <div key={key}><strong>{value}</strong><span>{key.replaceAll("_", " ")}</span></div>)}</div><ul>{result.outcomes.map((item) => <li key={item.operation_id}><code>{item.operation_id.slice(0, 12)}</code>: {item.execution.replaceAll("_", " ")} / access {item.verification.replaceAll("_", " ")}<p>{item.receipt?.reason.replaceAll("_", " ")} {item.verification_evidence?.reason}</p>{item.verification_error && <p role="status">Receipt or verification read unavailable: {item.verification_error.code.replaceAll("_", " ")} at {new Date(item.verification_error.at).toLocaleString()}. No committed mutation is repeated.</p>}{item.verification_evidence && <small>Last access observation: {new Date(item.verification_evidence.observed_at).toLocaleString()}, source revision {item.verification_evidence.revision}.</small>}</li>)}</ul><button onClick={() => download(result)} disabled={!!busy}>Export saved plan and outcomes (no usable approval)</button></section>}
    {result && <div className="facility-actions"><button disabled={!!busy || result.stopped || !result.approved} onClick={() => void act(result.status === "paused" ? "Continuing the exact approved plan" : "Pausing new dispatch", async () => { const value = await client.pauseConnectedPlan(result.plan.plan_id, result.status === "paused"); save(value.result); })}>{result.status === "paused" ? "Continue approved plan" : "Pause new dispatch"}</button>{(result.counts.outcome_unknown ?? 0) > 0 && <button disabled={!!busy || result.stopped || expired} onClick={() => void act("Looking up receipts before a bounded same-operation retry", async () => { const value = await client.executeConnectedPlan(result.plan.plan_id, true); save(value.result); })}>Retry unresolved operations with their original IDs</button>}<small>Paused or stopped in-flight requests may still finish. Reconciliation never repeats a committed mutation.</small></div>}
    {!!plans.length && <details><summary>Saved connected plans ({plans.length})</summary><ul>{plans.map((item) => <li key={item.plan.plan_id}><button disabled={!!busy} onClick={() => void act("Reading persisted plan", async () => { const value = await client.connectedPlan(item.plan.plan_id, { force: true }); save(value.result); })}>{item.plan.plan_id.slice(0, 12)} / {item.plan.identity.environment} / {item.status.replaceAll("_", " ")}</button></li>)}</ul></details>}
    {busy && <p role="status">{busy}...</p>}{error && <ErrorPanel message={error} />}
  </section>;
}

function mask(value: string | null | undefined) { return value ? `${value.slice(0, 4)}...${value.slice(-4)}` : "unresolved"; }
function download(result: ConnectedPlan) {
  const value = { format: "syndra.connected-report/v1", plan: result.plan, outcomes: result.outcomes, counts: result.counts };
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = `syndra-connected-${result.plan.plan_id.slice(0, 12)}.json`; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
