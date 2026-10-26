import { ArrowLeft, ArrowRight, Check, ChevronDown, CircleAlert, Download, FileCheck2, RefreshCw, Search, Sparkles, Upload, Users, Wrench } from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent, type CSSProperties } from "react";
import type { StudioClient } from "../api/client";
import type { RepairAccountResponse, RepairBatch, RepairCase, RepairCaseSummary, RepairEvent, RepairObservation, RepairPreview } from "../api/types";
import { ErrorPanel, LoadingState } from "../components/Primitives";
import { useStudio } from "../state/StudioContext";
import "./repair.css";

const labels: Record<string, string> = { repairable: "Needs correction", already_correct: "Already correct", ineligible: "Leave unchanged", unresolved: "Needs investigation", out_of_scope: "Outside incident", restored: "Access restored", conflict: "Changed since preview" };

export function RepairPage({ caseId }: { caseId?: string }) {
  const { client } = useStudio();
  useEffect(() => {
    const previous = document.title;
    document.title = "Customer repair | Syndra";
    return () => { document.title = previous; };
  }, []);
  if (!client) return null;
  return <div className="page repair-page">{caseId ? <RepairWorkspace key={caseId} client={client} caseId={caseId} /> : <RepairLanding client={client} />}</div>;
}

function useOperation() {
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const active = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  async function perform<T>(label: string, operation: () => Promise<T>, commit: (result: T) => void) {
    if (active.current) return;
    active.current = true; setWorking(label); setError("");
    try { const result = await operation(); if (alive.current) commit(result); }
    catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : "The operation could not be confirmed. Read current records before continuing."); }
    finally { active.current = false; if (alive.current) setWorking(""); }
  }
  return { working, error, setError, perform };
}

function RepairLanding({ client }: { client: StudioClient }) {
  const [cases, setCases] = useState<RepairCaseSummary[]>([]);
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [historyError, setHistoryError] = useState("");
  const [revision, setRevision] = useState(0);
  const file = useRef<HTMLInputElement>(null);
  const operation = useOperation();
  useEffect(() => {
    const controller = new AbortController();
    void client.repairCases(offset, { signal: controller.signal, force: true }).then((result) => {
      if (controller.signal.aborted) return;
      setCases((current) => offset ? [...current, ...result.cases.filter((item) => !current.some((old) => old.case_id === item.case_id))] : result.cases);
      setMore(result.cases.length === 50); setHistoryError("");
    }).catch(() => { if (!controller.signal.aborted) setHistoryError("Saved cases could not be read. Retry to inspect previous work."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [client, offset, revision]);
  function open(value: { case: RepairCase }) { window.location.assign(`#/repair/${value.case.summary.case_id}`); }
  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    event.target.value = "";
    if (!selected) return;
    void operation.perform("Reading exported records", async () => {
      if (selected.size > 750_000) throw new Error("Choose a JSON snapshot smaller than 750 KB, with at most 500 purchase records.");
      let data: unknown;
      try { data = JSON.parse(await selected.text()); } catch { throw new Error("This file is not valid JSON. Download the template to check the expected format."); }
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Choose a purchase-to-access snapshot using the documented template.");
      if ("snapshot" in data && Object.keys(data).length !== 1) throw new Error("The snapshot wrapper must contain only the snapshot field. Remove unsupported fields and import again.");
      const snapshot = "snapshot" in data ? (data as { snapshot: unknown }).snapshot : data;
      return client.importRepair(snapshot);
    }, open);
  }
  return <>
    <header className="repair-intro"><p className="eyebrow"><Wrench size={14} aria-hidden="true" />Syndra customer repair</p><h1>The code is fixed.<br /><span>Make the people whole.</span></h1><p>A successful fix can still leave yesterday's customers waiting. Find the missing access, review each correction, and see who still needs help.</p></header>
    <section className="repair-entry" aria-label="Start a customer repair">
      <div className="repair-entry-main"><span className="repair-edition">01 / Purchase to access</span><h2>They paid.<br />The door stayed shut.</h2><p>Explore a real local application with 100 synthetic purchase records. The new code works. The historical accounts still need attention.</p><button className="button button-primary" disabled={!!operation.working} onClick={() => void operation.perform("Creating local application", () => client.createRepairDemo(), open)}>Try the working demo<ArrowRight size={17} aria-hidden="true" /></button><small>No API key. No model calls. Changes affect this owned demo only.</small></div>
      <div className="repair-entry-art" aria-hidden="true"><div className="repair-thread" /><div className="repair-doors">{Array.from({ length: 100 }, (_, index) => <i key={index} className={index < 65 ? "closed" : index < 80 ? "open" : "unknown"} style={{ "--door": index % 10 } as CSSProperties} />)}</div><span>100 purchases. Different endings.</span></div>
    </section>
    {operation.working && <p role="status" className="repair-notice">{operation.working}...</p>}
    {operation.error && <ErrorPanel message={operation.error} />}
    <section className="repair-import" aria-label="Bring exported records"><div><p className="eyebrow">Your evidence</p><h2>Start with the records you have.</h2><p>Import a purchase-to-access snapshot and a reviewed fix reference. Analysis uses the supplied facts; source systems stay unchanged. Export the proposed corrections for your application's own process.</p><small>Up to 500 records / 750 KB. Use stable, pseudonymous account and payment IDs. Omit names, emails, credentials, and raw payloads.</small></div><div className="repair-import-actions"><input ref={file} aria-label="Purchase snapshot JSON file" type="file" accept=".json,application/json" onChange={(event) => void importFile(event)} disabled={!!operation.working} /><button disabled={!!operation.working} onClick={() => file.current?.click()}><Upload size={16} aria-hidden="true" />Import records</button><button disabled={!!operation.working} onClick={() => void operation.perform("Preparing template", () => client.repairTemplate(), (value) => downloadJson(value.snapshot, "syndra-purchase-access-template.json"))}><Download size={16} aria-hidden="true" />Download template</button></div></section>
    <section className="repair-history" aria-label="Saved customer repair cases"><header><div><p className="eyebrow">Pick up where you left off</p><h2>Saved cases</h2></div><button disabled={loading} onClick={() => { setOffset(0); setLoading(true); setRevision((value) => value + 1); }}><RefreshCw size={15} aria-hidden="true" />Refresh cases</button></header>
      {loading && <LoadingState label="Reading saved customer cases" />}{historyError && <ErrorPanel message={historyError} />}
      {!loading && !historyError && !cases.length && <p className="repair-empty">Your first case starts above. Cases and correction results are saved locally.</p>}
      <ol>{cases.map((item) => <li key={item.case_id}><a href={`#/repair/${item.case_id}`}><span><strong>{item.title}</strong><small>{item.mode === "local_demo" ? "Synthetic local application" : "Imported snapshot"} / {item.record_count} records / {item.batch_count} saved batches</small><small><time dateTime={item.created_at}>{date(item.created_at)}</time> / Case {item.case_id.slice(0, 12)}</small></span><ArrowRight size={18} aria-hidden="true" /></a></li>)}</ol>
      {more && <button disabled={loading} onClick={() => { setLoading(true); setOffset((value) => value + 50); }}>Load more cases</button>}
    </section>
  </>;
}

function RepairWorkspace({ client, caseId }: { client: StudioClient; caseId: string }) {
  const [current, setCurrent] = useState<RepairCase>();
  const [preview, setPreview] = useState<RepairPreview>();
  const [batches, setBatches] = useState<RepairBatch[]>([]);
  const [events, setEvents] = useState<RepairEvent[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [focused, setFocused] = useState<string>();
  const [confirmed, setConfirmed] = useState(false);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [loadRevision, setLoadRevision] = useState(0);
  const [pendingRequest, setPendingRequest] = useState(() => readPending(caseId));
  const pendingApply = useRef(pendingRequest);
  const [clock, setClock] = useState(() => Date.now());
  const [activeResult, setActiveResult] = useState("");
  const [accountRead, setAccountRead] = useState<RepairAccountResponse>();
  const resultHeading = useRef<HTMLHeadingElement>(null);
  const operation = useOperation();
  useEffect(() => {
    const controller = new AbortController();
    const options = { signal: controller.signal, force: true };
    void Promise.all([client.repairCase(caseId, options), client.currentRepairPreview(caseId, options), client.repairBatches(caseId, options), client.repairEvents(caseId, options)]).then(([value, saved, history, trail]) => {
      if (controller.signal.aborted) return;
      setCurrent(value.case); setPreview(saved.preview ?? undefined); setBatches(history.batches); setEvents(trail.events);
      setSelected(new Set()); setLoadError("");
    }).catch((reason) => { if (!controller.signal.aborted) setLoadError(reason instanceof Error ? reason.message : "This case could not be read."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [client, caseId, loadRevision]);
  useEffect(() => {
    if (!preview) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [preview]);
  useEffect(() => {
    if (activeResult) { resultHeading.current?.focus({ preventScroll: true }); resultHeading.current?.scrollIntoView?.({ block: "start" }); }
  }, [activeResult]);
  const expired = !!preview && clock >= Date.parse(preview.expires_at);
  const completedPreview = batches.some((batch) => batch.preview_id === preview?.preview_id);
  const latest = batches[0];
  const results = new Map([...batches].reverse().filter((batch) => batch.preview_id === preview?.preview_id).flatMap((batch) => batch.results).map((result) => [result.purchase_id, result]));
  const rows = preview?.observations ?? [];
  const visible = rows.filter((row) => (filter === "all" || (results.get(row.purchase_id)?.outcome ?? row.disposition) === filter) && `${row.purchase_id} ${row.tenant_id} ${row.account_id ?? ""} ${row.entitlement}`.toLowerCase().includes(search.trim().toLowerCase()));
  const focus = rows.find((row) => row.purchase_id === focused);
  function clearPending() { pendingApply.current = ""; writePending(caseId, ""); setPendingRequest(""); }
  function recordBatch(batch: RepairBatch) {
    setBatches((value) => [batch, ...value.filter((item) => item.request_id !== batch.request_id)]);
    setSelected(new Set()); setConfirmed(false); clearPending(); setActiveResult(batch.request_id); setAccountRead(undefined);
    setNotice(`${verifiedText(batch.restored_count)}. ${batch.skipped_count} selected records left unchanged. ${batch.unresolved_count} selected records need investigation.`);
  }
  function makePreview() {
    setNotice("");
    void operation.perform("Reading current records", async () => {
      const next = await client.previewRepair(caseId);
      const [history, trail, value] = await Promise.all([client.repairBatches(caseId, { force: true }), client.repairEvents(caseId, { force: true }), client.repairCase(caseId, { force: true })]);
      return { next, history, trail, value };
    }, ({ next, history, trail, value }) => {
      setCurrent(value.case); setPreview(next.preview ?? undefined); setBatches(history.batches); setEvents(trail.events);
      setSelected(new Set(next.preview?.observations.filter((row) => row.disposition === "repairable").slice(0, 200).map((row) => row.purchase_id)));
      setConfirmed(false); setClock(Date.now()); setFilter("all"); setFocused(undefined); clearPending(); setAccountRead(undefined);
      setNotice("A new preview is ready. Review the proposed corrections before applying.");
    });
  }
  function apply() {
    if (!preview || !selected.size || !confirmed || pendingApply.current || expired || completedPreview) return;
    const requestId = crypto.randomUUID().replaceAll("-", "");
    pendingApply.current = requestId; writePending(caseId, requestId); setPendingRequest(requestId); setNotice("");
    void operation.perform("Applying selected corrections", () => client.applyRepair(caseId, { preview_id: preview.preview_id, preview_fingerprint: preview.fingerprint, request_id: requestId, purchase_ids: [...selected], confirmation: "APPLY_SELECTED_CORRECTIONS" }), (response) => recordBatch(response.batch));
  }
  function choose(purchaseId: string, enabled: boolean) {
    setSelected((currentSelection) => { const next = new Set(currentSelection); if (enabled && next.size < 200) next.add(purchaseId); else if (!enabled) next.delete(purchaseId); return next; });
    setConfirmed(false);
  }
  if (loading) return <LoadingState label="Opening saved customer case" />;
  if (loadError || !current) return <><a className="repair-back" href="#/repair"><ArrowLeft size={15} aria-hidden="true" />Customer repair</a><ErrorPanel message={loadError || "The case is unavailable."} /><button onClick={() => { setLoading(true); setLoadRevision((value) => value + 1); }}>Retry case</button></>;
  const demo = current.summary.mode === "local_demo";
  return <>
    <a className="repair-back" href="#/repair"><ArrowLeft size={15} aria-hidden="true" />All customer cases</a>
    <header className="repair-case-intro"><p className="eyebrow"><span className="repair-live-dot" aria-hidden="true" />{demo ? "Synthetic local application" : "Imported snapshot / source unchanged"}</p><h1>{current.summary.title}</h1><p>Fix the software. Then finish the work for the people it affected.</p><code>Case {caseId.slice(0, 12)}</code><small>Times shown in {Intl.DateTimeFormat().resolvedOptions().timeZone}.</small></header>
    <nav className="repair-journey" aria-label="Customer repair steps"><a href="#repair-fix" onClick={(event) => { event.preventDefault(); document.getElementById("repair-fix")?.scrollIntoView({ block: "start" }); }}>01 <span>The fix</span></a><a href="#repair-records" onClick={(event) => { event.preventDefault(); document.getElementById("repair-records")?.scrollIntoView({ block: "start" }); }}>02 <span>Affected records</span></a><a href="#repair-outcome" onClick={(event) => { event.preventDefault(); document.getElementById("repair-outcome")?.scrollIntoView({ block: "start" }); }}>03 <span>Corrections & results</span></a></nav>
    <section className="repair-fix" id="repair-fix" aria-label="Fix and incident evidence"><div><p className="eyebrow"><FileCheck2 size={15} aria-hidden="true" />The reviewed fix</p><h2>{demo ? "Tomorrow works. Yesterday still needs help." : "A correction starts with a clear rule."}</h2><p>For this contract, a verified paid purchase should have its matching account access. Refunded and cancelled purchases do not receive restored access. Existing access is never revoked.</p><dl><div><dt>Incident, start included</dt><dd><time dateTime={current.snapshot.incident_start}>{date(current.snapshot.incident_start)}</time></dd></div><div><dt>Incident, end excluded</dt><dd><time dateTime={current.snapshot.incident_end}>{date(current.snapshot.incident_end)}</time></dd></div><div><dt>Fix reference</dt><dd><code>{current.snapshot.fix.before_revision.slice(0, 12)} to {current.snapshot.fix.after_revision.slice(0, 12)}</code></dd></div></dl><small>{demo ? "Fixed fixture behavior, executed locally. No model generated this example." : "Review and history completeness are declarations in your export; Syndra does not independently authenticate them."}</small></div>
      {current.demo_proof && <div className="repair-proof"><span className="repair-proof-check"><Check size={16} aria-hidden="true" />New purchase: access verified</span><strong>{current.demo_proof.historical_missing_after_fix}</strong><span>historical records still missing access after the code fix</span><details><summary>See the fixture's change<ChevronDown size={14} aria-hidden="true" /></summary><p>Illustrative handler excerpt; the demo exercises real SQLite purchase and access operations.</p><pre><code>{current.demo_proof.before_code}</code></pre><pre><code>{current.demo_proof.after_code}</code></pre></details></div>}
    </section>
    <section id="repair-records" className="repair-evidence" aria-label="Affected purchase records"><header><div><p className="eyebrow">02 / Evidence before action</p><h2>Who still needs help?</h2><p>{preview ? `Preview observed ${date(preview.observed_at)}.` : "Compare the incident records against the purchase-to-access rule."} {demo ? "The local application is checked again during apply." : "Imported facts describe the observation time, not a live account read."}</p></div><button disabled={!!operation.working} onClick={makePreview}><RefreshCw size={16} aria-hidden="true" />{preview ? "Preview current records" : "Find affected records"}</button></header>
      {!current.snapshot.coverage_complete && <p className="repair-attention"><strong>Partial evidence.</strong> These counts cover supplied records only. Other affected customers may be absent.</p>}
      {preview && <><div className="repair-counts" aria-label="Preview record counts">{(["repairable", "already_correct", "ineligible", "unresolved"] as const).map((kind) => <button type="button" key={kind} aria-pressed={filter === kind} className={`state-${kind}`} onClick={() => setFilter((value) => value === kind ? "all" : kind)}><strong>{preview.counts[kind]}</strong><span>{labels[kind]}</span></button>)}</div>
        <div className="repair-map-layout"><ImpactMap rows={rows} results={results} focused={focused} onFocus={setFocused} /><div className="repair-map-caption"><Users size={24} aria-hidden="true" /><h3>{focus ? focus.account_id ?? "Mapping needs investigation" : "A record is a person's experience."}</h3><p>{focus ? (results.get(focus.purchase_id)?.reason ?? focus.reason) : "Explore a square to inspect its evidence. Use arrow keys to move through the first 100 records, or use the searchable list below."}</p>{focus && <dl><div><dt>Purchase</dt><dd>{focus.purchase_id}</dd></div><div><dt>Workspace</dt><dd>{focus.tenant_id}</dd></div><div><dt>Entitlement</dt><dd>{focus.entitlement}</dd></div></dl>}<small>{rows.length} purchase records in this preview. Multiple purchases can refer to the same account.</small></div></div>
        {focus && demo && <section className="repair-account-peek" aria-label="Demo account access"><div><p className="eyebrow">The customer's side</p><strong>{focus.account_id ?? "Account mapping unavailable"}</strong><p>Check what the owned application grants this account right now.</p></div><button disabled={!!operation.working} onClick={() => void operation.perform("Reading the demo account", () => client.repairAccount(caseId, focus.purchase_id, { force: true }), setAccountRead)}>Read demo account</button>{accountRead?.purchase_id === focus.purchase_id && <p className={accountRead.allowed ? "state-restored" : "state-unresolved"} role="status"><strong>{accountRead.message}</strong><small>Last account read: {date(accountRead.observed_at)}. Read again after further changes.</small></p>}</section>}
        <div className="repair-record-toolbar"><label><Search size={15} aria-hidden="true" /><span className="sr-only">Search purchase records</span><input type="search" maxLength={160} placeholder="Find a purchase, account, or workspace" value={search} onChange={(event) => setSearch(event.target.value)} /></label><label><span className="sr-only">Record status</span><select aria-label="Record status" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">All records</option>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><span>{visible.length} of {rows.length} records</span>{(search || filter !== "all") && <button onClick={() => { setSearch(""); setFilter("all"); }}>Clear record filters</button>}</div>
        <div className="repair-record-list" aria-label="Purchase record list">{visible.length ? visible.map((row) => {
          const result = results.get(row.purchase_id);
          const status = result?.outcome ?? row.disposition;
          const selectable = demo && row.disposition === "repairable" && !completedPreview && !pendingRequest;
          return <article className={`repair-record state-${status}`} key={row.purchase_id}><div className="repair-record-choice">{selectable ? <input type="checkbox" aria-label={`Select ${row.purchase_id}`} checked={selected.has(row.purchase_id)} disabled={!!operation.working || (!selected.has(row.purchase_id) && selected.size >= 200)} onChange={(event) => choose(row.purchase_id, event.target.checked)} /> : <span aria-hidden="true" className="repair-record-marker" />}</div><div><strong>{row.account_id ?? "Account mapping missing"}</strong><small>{row.purchase_id} / {row.tenant_id}</small><p>{result?.reason ?? row.reason}</p><details><summary>Source references</summary><p>{row.evidence_ids.join(", ")}</p><small>Observed revision {row.observed_revision}. Entitlement: {row.entitlement}.</small></details></div><span className="repair-status">{labels[status]}</span></article>;
        }) : <p className="repair-empty">No records match. Clear the filters to see the entire preview.</p>}</div>
      </>}
    </section>
    {preview && <section className="repair-outcome" id="repair-outcome" aria-label="Corrections and results"><header><p className="eyebrow">03 / Finish the customer work</p><h2 ref={resultHeading} tabIndex={-1}>{latest ? "The results are saved." : demo ? "Give them the access they paid for." : "Take the proposed corrections with you."}</h2></header>
      {latest && <div className={`repair-result-summary ${latest.status === "needs_attention" ? "has-open-work" : ""}`} role="status">{latest.status === "needs_attention" ? <CircleAlert size={24} aria-hidden="true" /> : <Check size={24} aria-hidden="true" />}<div><strong>{verifiedText(latest.restored_count)}</strong><p>{latest.skipped_count} selected records left unchanged. {latest.unresolved_count} selected records need investigation.</p><small>This is the latest batch, not a claim that the whole incident is resolved.</small></div></div>}
      {demo && !completedPreview && <><div className="repair-selection"><span>{selected.size} proposed records selected. Up to 200 per batch.</span><button disabled={!!operation.working || !!pendingRequest} onClick={() => { setSelected(new Set(rows.filter((row) => row.disposition === "repairable").slice(0, 200).map((row) => row.purchase_id))); setConfirmed(false); }}>Select proposed records</button><button disabled={!!operation.working} onClick={() => { setSelected(new Set()); setConfirmed(false); }}>Clear selection</button></div><label className="repair-confirm"><input type="checkbox" checked={confirmed} disabled={!!operation.working || !!pendingRequest} onChange={(event) => setConfirmed(event.target.checked)} /><span>I reviewed the selected corrections. Apply missing access to this synthetic local application.</span></label><button className="button button-primary" disabled={!confirmed || !selected.size || !!operation.working || expired || !!pendingRequest} onClick={apply}>Apply {selected.size} selected {selected.size === 1 ? "correction" : "corrections"}<ArrowRight size={16} aria-hidden="true" /></button></>}
      {completedPreview && <p>To inspect what still needs help, choose <strong>Preview current records</strong>. This preview's corrections have a saved result.</p>}
      {expired && !completedPreview && <p className="repair-attention">This preview expired. Read current records again before applying corrections.</p>}
      {!demo && <p>Imported records support analysis and export. Applying to a real application needs a supported adapter and current evidence. The package contains proposed corrections; it does not prove they were applied.</p>}
      {pendingRequest && <div className="repair-attention" role="status"><strong>The apply response is not confirmed.</strong><p>Read the saved result for this request. You can also preview current records to reconcile the application before selecting again.</p><code>{pendingRequest}</code><button disabled={!!operation.working} onClick={() => void operation.perform("Checking saved result", () => client.repairBatch(caseId, pendingRequest, { force: true }), (value) => recordBatch(value.batch))}>Check saved result</button></div>}
      <div className="repair-export"><button disabled={!!operation.working} onClick={() => void operation.perform("Preparing correction package", () => client.exportRepair(caseId, preview.preview_id), (value) => { downloadJson(value.package, `syndra-repair-${caseId.slice(0, 12)}.json`); setNotice("Correction package downloaded. The checksum checks consistency, not source truth or authorship."); })}><Download size={16} aria-hidden="true" />Export this preview & results</button><small>The package includes supplied pseudonymous records, observations, and batches for this preview.</small></div>
      {demo && !completedPreview && <details className="repair-demo-controls"><summary><Sparkles size={15} aria-hidden="true" />Try a change after preview</summary><p>These controls change only this synthetic application. Apply rechecks each selected record, so later support work and refunds are respected.</p><div><button disabled={!!operation.working || !!pendingRequest} onClick={() => void operation.perform("Simulating support corrections", () => client.repairDemoChange(caseId, "support_corrects_five"), (value) => setNotice(`${value.affected_count} accounts were corrected by support. The saved preview is unchanged; apply will check them again.`))}>Support fixes five accounts</button><button disabled={!!operation.working || !!pendingRequest} onClick={() => void operation.perform("Simulating a refund", () => client.repairDemoChange(caseId, "refund_one"), (value) => setNotice(`${value.affected_count} purchase was refunded. Apply will check the current payment state.`))}>Refund the first purchase</button></div></details>}
    </section>}
    {operation.working && <p className="repair-notice" role="status">{operation.working}...</p>}{notice && <p className="repair-notice" role="status">{notice}</p>}{operation.error && <ErrorPanel message={operation.error} />}
    <details className="repair-history-detail"><summary>Saved batches & case activity</summary>{batches.map((batch) => <section key={batch.request_id}><h3>{batch.restored_count} restored / {batch.skipped_count} unchanged / {batch.unresolved_count} unresolved</h3><time dateTime={batch.created_at}>{date(batch.created_at)}</time><code>Request {batch.request_id}</code><details><summary>Inspect this batch's {batch.selected_count} results</summary><ul>{batch.results.map((result) => <li key={result.purchase_id}><strong>{result.purchase_id}: {labels[result.outcome]}</strong><p>{result.reason}</p></li>)}</ul></details></section>)}<ol>{events.map((event) => <li key={event.sequence}><time dateTime={event.created_at}>{date(event.created_at)}</time> / {event.kind.replaceAll("_", " ")} / {event.affected_count} records</li>)}</ol><button disabled={!!operation.working} onClick={() => void operation.perform("Reading case activity", () => client.repairEvents(caseId, { force: true }), (value) => setEvents(value.events))}>Refresh case activity</button></details>
  </>;
}

function ImpactMap({ rows, results, focused, onFocus }: { rows: RepairObservation[]; results: Map<string, RepairBatch["results"][number]>; focused?: string; onFocus: (id: string) => void }) {
  const refs = useRef(new Map<number, HTMLButtonElement>());
  const subset = rows.slice(0, 100);
  const [active, setActive] = useState(0);
  return <div className="repair-impact-map" role="grid" aria-label={`Evidence map, first ${subset.length} purchase records`} aria-rowcount={Math.ceil(subset.length / 10)} aria-colcount={10}>{Array.from({ length: Math.ceil(subset.length / 10) }, (_, rowIndex) => <div role="row" key={rowIndex}>{subset.slice(rowIndex * 10, rowIndex * 10 + 10).map((row, column) => {
    const index = rowIndex * 10 + column;
    const status = results.get(row.purchase_id)?.outcome ?? row.disposition;
    return <div role="gridcell" key={row.purchase_id} aria-selected={focused === row.purchase_id}><button ref={(node) => { if (node) refs.current.set(index, node); else refs.current.delete(index); }} type="button" className={`state-${status}`} tabIndex={index === active ? 0 : -1} aria-label={`${row.purchase_id}: ${labels[status]}`} onFocus={() => { setActive(index); onFocus(row.purchase_id); }} onClick={() => onFocus(row.purchase_id)} onKeyDown={(event) => {
      const offsets: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 10, ArrowUp: -10 };
      if (event.key in offsets || event.key === "Home" || event.key === "End") {
        event.preventDefault(); const next = event.key === "Home" ? 0 : event.key === "End" ? subset.length - 1 : Math.min(subset.length - 1, Math.max(0, index + offsets[event.key]));
        refs.current.get(next)?.focus();
      }
    }}><span aria-hidden="true">{status === "restored" || status === "already_correct" ? <Check size={10} /> : status === "unresolved" ? "?" : ""}</span></button></div>;
  })}</div>)}</div>;
}

function date(value: string) { return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); }
function verifiedText(count: number) { return `${count} access ${count === 1 ? "correction" : "corrections"} verified`; }
function readPending(caseId: string) { try { const value = sessionStorage.getItem(`syndra.repair.request.${caseId}`) ?? ""; return /^[a-f0-9]{32}$/.test(value) ? value : ""; } catch { return ""; } }
function writePending(caseId: string, value: string) { try { if (value) sessionStorage.setItem(`syndra.repair.request.${caseId}`, value); else sessionStorage.removeItem(`syndra.repair.request.${caseId}`); } catch { /* Saved backend results remain authoritative. */ } }
function downloadJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + "\n"], { type: "application/json" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
