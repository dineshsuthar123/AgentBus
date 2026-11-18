import { useEffect, useRef, useState } from "react";
import type { StudioClient } from "../api/client";
import type { ConnectionStatus } from "../api/types";
import { ErrorPanel } from "./Primitives";
import "../pages/repair.css";

export function RepairConnections({ client }: { client: StudioClient }) {
  const [items, setItems] = useState<ConnectionStatus[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const active = useRef(false);
  useEffect(() => {
    const abort = new AbortController();
    async function load() {
      try { const value = await client.repairConnections({ signal: abort.signal, force: true }); if (!abort.signal.aborted) setItems(value.connections); }
      catch { if (!abort.signal.aborted) setError("Customer-repair connections could not be read. Reconnect or refresh Runtime health."); }
    }
    void load(); return () => abort.abort();
  }, [client]);
  async function check(id: string) {
    if (active.current) return;
    active.current = true; setBusy(id); setError("");
    try { const value = await client.checkRepairConnection(id); setItems((old) => old.map((item) => item.manifest.connection_id === id ? value.connection : item)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The target identity could not be verified."); }
    finally { active.current = false; setBusy(""); }
  }
  return <section className="repair-connections" aria-label="Application correction connections"><p className="eyebrow">Application-owned integrations</p><h2>Customer repair connections</h2><p>Production credentials stay in the local keychain or daemon environment. A configured credential is not a successful connection check.</p>
    <details><summary>Install and connect your application</summary><ol><li>Install <code>syndra[repair]</code> in the application service. Mount the versioned correction integration behind HTTPS.</li><li>Implement the documented purchase mapping, aggregate writer hooks and normal access check. Run the adversarial and deployment conformance checks.</li><li>Keep a scoped service credential in the OS keychain or a <code>SYNDRA_REPAIR_*</code> daemon variable. Never paste it here.</li><li>Register a reviewed identity, pinned destination and credential reference with <code>syndra repair connection-add manifest.json --workspace .</code>.</li><li>Check this connection and start with one approved canary.</li></ol><p>Integration and least-privilege setup: <code>docs/product/production-customer-repair.md</code>.</p></details>
    {error && <ErrorPanel message={error} />}{!items.length && <p>No application connections are configured. The guided synthetic demo remains available.</p>}
    {items.map((item) => <article key={item.manifest.connection_id}><header><strong>{item.manifest.connection_id}</strong><span className={`repair-environment env-${item.manifest.identity.environment}`}>{item.manifest.identity.environment}</span></header><dl><div><dt>Application / tenant</dt><dd>{item.manifest.identity.application} / {item.manifest.identity.tenant}</dd></div><div><dt>Approved destination</dt><dd><code>{item.manifest.destination}</code></dd></div><div><dt>Credential</dt><dd>{item.configured ? "Configured" : "Missing or unchecked"} / reference only</dd></div><div><dt>Permissions</dt><dd>{item.capabilities?.permissions.join(", ") ?? item.manifest.permissions.join(", ")} {item.capabilities ? "(authenticated)" : "(requested)"}</dd></div><div><dt>Last check</dt><dd>{item.checked_at ? new Date(item.checked_at).toLocaleString() : "Never checked"}</dd></div></dl><p role="status">{item.write_ready ? "Conditional writes available. " : "Apply unavailable. "}{item.reason}</p><button disabled={!!busy} onClick={() => void check(item.manifest.connection_id)}>{busy === item.manifest.connection_id ? "Checking identity..." : "Check application connection"}</button></article>)}
  </section>;
}
