import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { StudioClient } from "../api/client";
import type { ContextPlanSummary, ImpactResult, McpServerCheckResponse, McpServerListResponse, RepositorySearchReport, RunSummary, SymbolSummary, ToolListResponse, ToolPolicyResponse, WorkspaceGraphResponse, WorkspaceIndexStatusResponse } from "../api/types";
import { useStudio } from "../state/StudioContext";
import { ErrorPanel, LoadingState } from "./Primitives";

const RuntimePage = lazy(() => import("../pages/RuntimePage").then((module) => ({ default: module.RuntimePage })));

const sections = ["connections", "repository", "tools", "policy", "mcp"] as const;
type Section = typeof sections[number];

export function WorkspaceDrawer({ query, run }: { query: URLSearchParams; run?: RunSummary }) {
  const { client } = useStudio();
  const requested = query.get("drawer");
  const section = sections.find((value) => value === requested) ?? "connections";
  return <>
    <nav className="drawer-sections" aria-label="Workspace sections">{sections.map((value) => <button key={value} aria-pressed={section === value} onClick={() => selectDrawer(value)}>{value === "mcp" ? "MCP" : value[0].toUpperCase() + value.slice(1)}</button>)}</nav>
    {section === "connections" ? <Suspense fallback={<LoadingState label="Opening connections" />}><RuntimePage /></Suspense> : !client ? <p>Reconnect before inspecting workspace facilities.</p> : section === "repository" ? run ? <RepositoryWorkspace key={run.workspace} client={client} run={run} /> : <p>Open a run to inspect its exact repository. Creating a task does not retarget an existing run.</p> : <ToolWorkspace key={section} client={client} section={section} />}
  </>;
}

function RepositoryWorkspace({ client, run }: { client: StudioClient; run: RunSummary }) {
  const [index, setIndex] = useState<WorkspaceIndexStatusResponse>();
  const [trusted, setTrusted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<RepositorySearchReport>();
  const [offset, setOffset] = useState(0);
  const [symbol, setSymbol] = useState<SymbolSummary>();
  const [graph, setGraph] = useState<WorkspaceGraphResponse>();
  const [impact, setImpact] = useState<ImpactResult>();
  const [context, setContext] = useState<ContextPlanSummary>();
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    let active = true;
    void client.attachIndex(run.workspace).then((value) => { if (active) setIndex(value); }).catch((reason: unknown) => { if (active) setError(message(reason)); });
    return () => { active = false; alive.current = false; };
  }, [client, run.workspace]);
  async function act(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(undefined); setNotice(undefined);
    try { await operation(); } catch (reason) { if (alive.current) setError(message(reason)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function refresh(workspaceId: string) {
    const result = await client.indexStatus(workspaceId, { force: true });
    if (alive.current) setIndex(result);
  }
  async function find(nextOffset = 0) {
    if (!index) return;
    const value = await client.searchIndex(index.workspace_id, query, nextOffset);
    if (alive.current) { setSearch(value.report); setOffset(nextOffset); setSymbol(undefined); setGraph(undefined); }
  }
  return <section className="workspace-facility" aria-label="Repository intelligence">
    <h3>Repository</h3><p>Scope is the selected run's original repository, not the daemon's launch directory.</p><code className="workspace-path">{run.workspace}</code>
    {error && <ErrorPanel message={error} />}{notice && <p role="status">{notice}</p>}
    <p role="status">Index: <strong>{index?.status.state ?? "Loading"}</strong>. {index?.status.message} {busy && "Request in progress; last snapshot retained."}</p>
    {index && <p>{index.status.indexed_files ?? 0} indexed files. Snapshot <code>{index.status.snapshot_id ?? "Not built"}</code></p>}
    <label className="workspace-trust"><input type="checkbox" checked={trusted} onChange={(event) => setTrusted(event.target.checked)} />I trust this repository for static local indexing. No repository code, model or shell is executed.</label>
    <div className="facility-actions">
      <button disabled={busy || !trusted} onClick={() => void act(async () => { const result = await client.buildIndex(run.workspace); await refresh(result.workspace_id); setSearch(undefined); setSymbol(undefined); setGraph(undefined); setImpact(undefined); setContext(undefined); })}>Build index</button>
      <button disabled={busy || !index || !trusted} onClick={() => void act(async () => { await client.indexAction(index!.workspace_id, "update"); await refresh(index!.workspace_id); })}>Update index</button>
      <button disabled={busy || !index} onClick={() => void act(async () => { const result = await client.verifyIndex(index!.workspace_id); await refresh(index!.workspace_id); if (alive.current) setNotice(`Integrity ${result.result.valid ? "valid" : "invalid"}; ${result.result.fresh ? "fresh" : "stale"}.`); })}>Verify index</button>
      <button disabled={busy || !index || !trusted} onClick={() => void act(async () => { await client.indexAction(index!.workspace_id, "repair"); await refresh(index!.workspace_id); })}>Repair index</button>
      <button disabled={!index} onClick={() => { void client.cancelIndex(index!.workspace_id).then((result) => { if (alive.current) setNotice(result.cancellation_requested ? "Index cancellation requested; wait for persisted status." : "No cancellable index operation."); }).catch((reason: unknown) => { if (alive.current) setError(message(reason)); }); }}>Cancel indexing</button>
    </div>
    {index?.status.diagnostics?.map((item, position) => <p key={`${item.code}:${position}`}>{item.severity}: {item.message}</p>)}
    <form className="repository-search" onSubmit={(event) => { event.preventDefault(); void act(() => find()); }}><label>Search indexed files and symbols<input maxLength={512} value={query} onChange={(event) => setQuery(event.target.value)} /></label><button disabled={busy || !index?.status.snapshot_id || !query.trim()}>Search repository</button></form>
    {search && <><p>Snapshot <code>{search.snapshot_id}</code> / {search.index_state}. Results {offset + 1}-{offset + (search.results?.length ?? 0)}; bounded pages of 25. Empty results are not proof of absence.</p><ul className="repository-results">{search.results?.map((item, position) => <li key={`${item.relative_path}:${position}`}><code>{item.relative_path}</code> {item.symbol ? <button disabled={busy} onClick={() => void act(async () => { const value = await client.symbol(index!.workspace_id, item.symbol!.symbol_id); if (alive.current) { setSymbol(value.symbol); setGraph(undefined); } })}>{item.symbol.qualified_name}</button> : "File match"}<p>{item.explanation}{item.stale ? " Stale evidence." : ""}</p></li>)}</ul><div className="facility-actions"><button disabled={busy || offset === 0} onClick={() => void act(() => find(Math.max(0, offset - 25)))}>Previous results</button><button disabled={busy || (search.results?.length ?? 0) < 25} onClick={() => void act(() => find(offset + 25))}>Next results</button></div></>}
    {symbol && <div><h4>{symbol.qualified_name}</h4><p>{symbol.kind} / {symbol.language} / <code>{symbol.relative_path}:{symbol.start_line}</code></p><button disabled={busy} onClick={() => void act(async () => { const value = await client.dependencies(index!.workspace_id, symbol.symbol_id); if (alive.current) setGraph(value); })}>Inspect dependencies</button></div>}
    {graph && <div><p>{graph.index_state}; {graph.total_edges} dependency edges. {graph.truncated && "Bounded view is truncated."}</p><ul>{graph.nodes?.map((node) => <li key={node.node_id}>{node.label}</li>)}</ul></div>}
    <div className="facility-actions"><button disabled={busy || !index?.status.snapshot_id || !run.changed_files?.length} onClick={() => void act(async () => { const value = await client.impact(index!.workspace_id, run.changed_files ?? []); if (alive.current) setImpact(value.result); })}>Inspect changed-file impact</button><button disabled={busy || !index?.status.snapshot_id} onClick={() => void act(async () => { const value = await client.contextPlan(index!.workspace_id, run.original_task); if (alive.current) setContext(value.result); })}>Plan task context</button></div>
    {impact && <div><h4>Change impact: {impact.risk}</h4><p>Confidence {impact.confidence}. {impact.truncated && "Analysis truncated."} Static analysis is not an executed test.</p><ul>{impact.uncertainty?.map((item) => <li key={item}>{item}</li>)}</ul><h4>Suggested tests</h4><ul>{impact.tests.selected_tests?.map((item) => <li key={item}><code>{item}</code></li>)}</ul>{impact.tests.full_suite_recommended && <p>Full test suite recommended.</p>}</div>}
    {context && <div><h4>Context plan</h4><p>{context.selected_bytes} / {context.byte_budget} bytes; {context.selected_tokens} / {context.token_budget} estimated tokens. {context.stale_warning}</p><ul>{context.candidates?.map((item) => <li key={item.candidate_id}><code>{item.relative_path}</code>: {item.selected ? "selected" : item.exclusion_reason ?? "excluded"}</li>)}</ul></div>}
  </section>;
}

function ToolWorkspace({ client, section }: { client: StudioClient; section: "tools" | "policy" | "mcp" }) {
  const [tools, setTools] = useState<ToolListResponse>();
  const [policy, setPolicy] = useState<ToolPolicyResponse>();
  const [servers, setServers] = useState<McpServerListResponse>();
  const [checks, setChecks] = useState<Record<string, McpServerCheckResponse>>({});
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    const options = { signal: controller.signal };
    const request = section === "tools" ? client.tools(options).then(setTools) : section === "policy" ? client.policy(options).then(setPolicy) : client.mcpServers(options).then(setServers);
    void request.catch((reason: unknown) => { if (!controller.signal.aborted) setError(message(reason)); });
    return () => controller.abort();
  }, [client, section]);
  async function check(serverId: string) {
    if (busy || !window.confirm("Run a bounded MCP diagnostic? This may start the configured local server process or contact its configured loopback endpoint; no advertised tool is invoked.")) return;
    setBusy(serverId); setError(undefined);
    try { const result = await client.checkMcp(serverId); setChecks((current) => ({ ...current, [serverId]: result })); }
    catch (reason) { setError(message(reason)); } finally { setBusy(undefined); }
  }
  return <section className="workspace-facility" aria-label={section === "mcp" ? "MCP servers" : section}>
    <h3>{section === "mcp" ? "MCP servers" : section[0].toUpperCase() + section.slice(1)}</h3><p>Effective daemon configuration. Edit trusted local configuration and restart deliberately to apply changes; this view cannot execute arbitrary commands or silently reload policy.</p>
    {error && <ErrorPanel message={error} />}
    {!error && !tools && !policy && !servers && <p role="status">Loading configuration...</p>}
    {tools && <><p>{tools.total} managed tools. Availability is not authorization; each invocation still passes capability, policy and exact approval checks.</p><ul>{tools.tools.map((tool) => <li key={tool.name}><details><summary><code>{tool.name}</code> / {tool.safety}</summary><p>{tool.description}</p><p>Version {tool.version.major}.{tool.version.minor}.{tool.version.patch}; timeout ceiling {tool.maximum_timeout_seconds}s; cancellation {tool.supports_cancellation ? "supported" : "not supported"}.</p><ul>{tool.capabilities.map((capability, position) => <li key={position}><code>{capability.name}</code></li>)}</ul></details></li>)}</ul></>}
    {policy && <><p>Policy <code>{policy.policy_id}</code>. Read-only diagnostic; no approval or execution is implied.</p>{policy.rules.map((rule, position) => <details key={position}><summary>{rule.rule_id ?? rule.id ?? `Rule ${position + 1}`}</summary><dl>{Object.entries(rule).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl></details>)}<details><summary>Effective bounded configuration</summary><pre>{JSON.stringify(policy.configuration, null, 2)}</pre></details></>}
    {servers && <>{!servers.total && <p>No MCP servers configured. Use trusted local configuration; secrets are not entered in Studio.</p>}<ul>{servers.servers.map((server) => <li key={server.server_id}><h4>{server.server_id}</h4><p>{server.transport} / {server.executable_alias ?? server.endpoint_host}; {server.configured_tools.length} configured tools.</p><button disabled={Boolean(busy)} onClick={() => void check(server.server_id)}>{busy === server.server_id ? "Checking..." : `Check ${server.server_id}`}</button>{checks[server.server_id] && <p role="status">{checks[server.server_id].ready ? "Ready" : "Not ready"}; checked {checks[server.server_id].checked_at}. {checks[server.server_id].message} Cleanup {checks[server.server_id].cleanup_completed ? "confirmed" : "not confirmed"}.</p>}</li>)}</ul></>}
  </section>;
}

function message(reason: unknown) { return reason instanceof Error ? reason.message : "Workspace request failed; no successful result is implied."; }

function selectDrawer(value: Section) {
  const [path, raw] = window.location.hash.split("?");
  const parameters = new URLSearchParams(raw);
  parameters.set("drawer", value);
  window.location.hash = `${path || "#/"}?${parameters}`;
}
