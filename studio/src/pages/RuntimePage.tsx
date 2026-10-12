import { Activity, CheckCircle2, CircleAlert, Cpu, Database, RadioTower, RefreshCw, Server, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { ErrorPanel, PageIntro, StatusSignal } from "../components/Primitives";
import { displayWorkspace, humanize, recordOf } from "../lib/format";
import { useStudio } from "../state/StudioContext";

export function RuntimePage() {
  const { client, doctor, providers, info, streamConnected, connection, connectionMessage, refresh } = useStudio();
  const [checking, setChecking] = useState<string>();
  const [checkError, setCheckError] = useState<string>();
  async function check(name: string) {
    if (!client || !window.confirm("Send one minimal model request? Azure may charge your configured account. No fallback is used.")) return;
    setChecking(name);
    try { await client.checkProvider(name); await refresh(); setCheckError(undefined); }
    catch (reason) { setCheckError(reason instanceof Error ? reason.message : "Provider check failed"); }
    finally { setChecking(undefined); }
  }
  const checks = doctor?.checks ?? [];
  return (
    <div className="page runtime-page">
      <PageIntro eyebrow="Local control plane" title="Runtime health" action={<button className="button button-secondary" type="button" onClick={() => void refresh()}><RefreshCw size={14} /> Refresh</button>}>
        Sanitized daemon diagnostics, provider readiness, and transport state from the authenticated loopback API.
      </PageIntro>
      {connectionMessage && <ErrorPanel message={connectionMessage} />}
      {checkError && <ErrorPanel message={checkError} />}
      <section className="runtime-hero">
        <div className={`runtime-orbit ${doctor?.status === "ok" || doctor?.status === "healthy" ? "is-healthy" : ""}`}><span><Cpu size={30} /></span><i /><i /><i /></div>
        <div><p className="section-label">Daemon state</p><h2>{humanize(doctor?.status ?? connection)}</h2><p>Installed Studio uses a process-local HTTP-only session. Native clients use bearer authentication. No credential is written to browser storage.</p></div>
        <dl><div><dt>Protocol</dt><dd><code>{String(info?.protocol_version ?? "--")}</code></dd></div><div><dt>Version</dt><dd>{String(info?.agentbus_version ?? "--")}</dd></div><div><dt>SSE</dt><dd><StatusSignal status={streamConnected ? "connected" : "snapshot"} /></dd></div></dl>
      </section>
      <div className="runtime-grid">
        <section className="content-section">
          <div className="section-heading"><div><p className="section-label">Doctor</p><h2>Managed storage &amp; tools</h2></div><Database size={18} /></div>
          <div className="doctor-list">{checks.map((raw, index) => <DoctorCheck value={recordOf(raw)} index={index} key={`${String(recordOf(raw).name)}-${index}`} />)}{!checks.length && <p className="quiet-copy">No doctor checks were returned.</p>}</div>
        </section>
        <section className="content-section">
          <div className="section-heading"><div><p className="section-label">Routes</p><h2>Model providers</h2></div><RadioTower size={18} /></div>
          <div className="runtime-providers">{providers.map((provider) => <article key={provider.name}><span className="provider-glyph"><Server size={18} /></span><div><strong>{provider.name === "deterministic" ? "Guided demo" : humanize(provider.name)}</strong><p>{provider.model ?? "No model configured"}</p><p>{provider.message}</p>{provider.endpoint_host && <code>{provider.endpoint_host}</code>}<p>Configuration: {provider.configuration_status ?? (provider.configured ? "valid" : "invalid")}. Reachability: {provider.reachable === null || provider.reachable === undefined ? "unknown" : provider.reachable ? "reachable" : "unreachable"}. Model: {provider.model_available === null || provider.model_available === undefined ? "not checked" : provider.model_available ? "available" : "unavailable"}.</p><p>Last check: {provider.checked_at ? new Date(provider.checked_at).toLocaleString() : "Not checked"}</p>{provider.name !== "deterministic" && <button disabled={!provider.configured || checking !== undefined} onClick={() => void check(provider.name)}>{checking === provider.name ? "Checking model..." : "Check model (live request)"}</button>}</div><StatusSignal status={provider.ready ? "ready" : provider.check_status === "failed" ? "failed" : provider.configured ? "not checked" : "unavailable"} /></article>)}</div>
        </section>
      </div>
      <section className="security-boundary"><ShieldCheck size={21} /><div><strong>Security boundary preserved</strong><p>Installed assets and API share an authenticated numeric-loopback origin. The contributor proxy is development-only. Cross-origin browser access stays rejected; secrets and unrestricted tool arguments are not rendered.</p></div></section>
    </div>
  );
}

function DoctorCheck({ value, index }: { value: Record<string, unknown>; index: number }) {
  const name = String(value.name ?? value.check ?? value.id ?? `check-${index + 1}`);
  const status = String(value.status ?? (value.ok === true ? "passed" : value.ok === false ? "failed" : "unknown"));
  const message = value.message ?? value.summary
    ? String(value.message ?? value.summary)
    : value.path
      ? `${displayWorkspace(String(value.path))} managed path is available.`
      : "No diagnostic detail reported.";
  const healthy = ["ok", "passed", "ready", "healthy", "writable"].some((item) => status.toLowerCase().includes(item));
  return <article className="doctor-check">{healthy ? <CheckCircle2 size={16} /> : status === "unknown" ? <Activity size={16} /> : <CircleAlert size={16} />}<div><strong>{humanize(name)}</strong><p>{message}</p></div><StatusSignal status={status} /></article>;
}
