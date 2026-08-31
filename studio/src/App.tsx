import {
  Activity,
  Command,
  Gauge,
  History,
  LogOut,
  Play,
  Plus,
  Radio,
  ShieldCheck
} from "lucide-react";
import { lazy, Suspense, useEffect, useState, type FormEvent, type ReactNode } from "react";
import type { RunSummary } from "./api/types";
import { CommandPalette } from "./components/CommandPalette";
import { LoadingState } from "./components/Primitives";
import { displayWorkspace, shortId } from "./lib/format";
import { useStudio } from "./state/StudioContext";

const DashboardPage = lazy(() => import("./pages/DashboardPage").then((module) => ({ default: module.DashboardPage })));
const DemoPage = lazy(() => import("./pages/DemoPage").then((module) => ({ default: module.DemoPage })));
const HistoryPage = lazy(() => import("./pages/HistoryPage").then((module) => ({ default: module.HistoryPage })));
const NewRunPage = lazy(() => import("./pages/NewRunPage").then((module) => ({ default: module.NewRunPage })));
const RunPage = lazy(() => import("./pages/RunPage").then((module) => ({ default: module.RunPage })));
const RuntimePage = lazy(() => import("./pages/RuntimePage").then((module) => ({ default: module.RuntimePage })));

interface RouteState {
  path: string;
  query: URLSearchParams;
}

export function App() {
  const studio = useStudio();
  const route = useHashRoute();

  if (!studio.client) return <ConnectionScreen />;

  const runMatch = /^\/runs\/([^/]+)$/.exec(route.path);
  const runId = runMatch ? decodeURIComponent(runMatch[1]) : undefined;
  const currentRun = runId ? studio.runs.find((run) => run.run_id === runId) : undefined;
  let page: ReactNode;
  if (runId) page = <RunPage runId={runId} />;
  else if (route.path === "/new") page = <NewRunPage demo={route.query.get("demo")} />;
  else if (route.path === "/history") page = <HistoryPage />;
  else if (route.path === "/runtime") page = <RuntimePage />;
  else if (route.path === "/demo") page = <DemoPage />;
  else page = <DashboardPage />;

  return (
    <div className="observatory-shell">
      <NavigationRail route={route.path} onDisconnect={studio.disconnect} />
      <header className="context-bar">
        <ContextIdentity run={currentRun} route={route.path} />
        <div className="context-actions">
          <span className={`transport-state phase-${studio.streamStatus.phase}`} title={`Event cursor ${studio.streamStatus.cursor}`}><Radio size={12} /><span>{streamLabel(studio.streamStatus.phase)}</span><code>{studio.streamStatus.cursor || "--"}</code></span>
          <CommandPalette currentRun={currentRun} />
        </div>
      </header>
      <main className="observatory-main" id="main-content">
        <Suspense fallback={<LoadingState label="Opening Studio surface" />}>{page}</Suspense>
      </main>
      <StudioAnnouncements runs={studio.runs} streamPhase={studio.streamStatus.phase} />
    </div>
  );
}

function NavigationRail({ route, onDisconnect }: { route: string; onDisconnect: () => void }) {
  return <aside className="navigation-rail">
    <a className="rail-brand" href="#/" aria-label="AgentBus Studio home"><img src="/agentbus-mark.svg" alt="" /><span>AB</span></a>
    <nav aria-label="Studio navigation">
      <NavLink route={route} href="#/" label="Command" icon={<Command size={18} />} />
      <NavLink route={route} href="#/new" label="New run" icon={<Plus size={18} />} />
      <NavLink route={route} href="#/history" label="History" icon={<History size={18} />} />
      <NavLink route={route} href="#/runtime" label="Runtime" icon={<Gauge size={18} />} />
      <NavLink route={route} href="#/demo" label="Payment proof" icon={<ShieldCheck size={18} />} />
    </nav>
    <button className="rail-disconnect" type="button" onClick={onDisconnect} aria-label="Disconnect Studio"><LogOut size={17} /><span>Disconnect</span></button>
  </aside>;
}

function ContextIdentity({ run, route }: { run?: RunSummary; route: string }) {
  const section = route === "/" ? "Command deck" : route.split("/").filter(Boolean)[0] ?? "Studio";
  return <div className="context-identity">
    <span><small>Repository</small><strong>{run ? displayWorkspace(run.workspace) : "Runtime scope"}</strong></span>
    <i />
    <span><small>Branch</small><strong>{run ? "Not reported" : "--"}</strong></span>
    <i />
    <span><small>{run ? "Run" : "Surface"}</small><strong className="mono">{run ? shortId(run.run_id, 6) : section}</strong></span>
  </div>;
}

function ConnectionScreen() {
  const { connect, connection, connectionMessage } = useStudio();
  const [token, setToken] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await connect(token);
      window.location.hash = connectionDestination(window.location.hash);
    } catch {
      // The context exposes only the bounded connection error.
    }
  }

  return (
    <main className="connection-screen">
      <section className="connection-story">
        <div className="brand-lockup brand-lockup-large"><img src="/agentbus-mark.svg" alt="" /><span><strong>AgentBus</strong><small>Studio</small></span></div>
        <p className="connection-kicker"><ShieldCheck size={15} /> Local execution control plane</p>
        <h1>See the agent.<br /><span>Trust the evidence.</span></h1>
        <p className="connection-lede"><strong>Safe execution for autonomous software engineering.</strong> Plan repository changes, hold risky tools at exact approval gates, and verify every outcome with durable evidence.</p>
        <div className="connection-rail" aria-label="AgentBus execution lifecycle">
          {["Plan", "Execute", "Approve", "Verify", "Review", "Replay"].map((stage) => <span key={stage}><i />{stage}</span>)}
        </div>
      </section>
      <section className="connection-panel">
        <div><p className="eyebrow">Loopback session</p><h2>Connect to AgentBus</h2><p>Start the authenticated local daemon, then enter its one-time bearer token. The token stays in browser memory.</p></div>
        <pre className="startup-command"><code>agentbus serve --port 8765 --json-ready</code></pre>
        <form onSubmit={(event) => void submit(event)}>
          <label><span>Session token</span><input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Paste bearer_token from the ready handshake" minLength={32} required /></label>
          {connectionMessage && <p className="inline-error" role="alert">{connectionMessage}</p>}
          <button className="button button-primary button-wide" disabled={connection === "connecting"} type="submit">{connection === "connecting" ? <Activity className="spin" size={16} /> : <Play size={16} />}{connection === "connecting" ? "Establishing control path" : "Open Studio"}</button>
        </form>
        <div className="security-note"><span>01</span><p><strong>Same-origin by design.</strong> Vite proxies only to the configured numeric-loopback daemon; AgentBus keeps its no-CORS security boundary.</p></div>
      </section>
    </main>
  );
}

function StudioAnnouncements({ runs, streamPhase }: { runs: RunSummary[]; streamPhase: string }) {
  const active = runs.find((run) => !["succeeded", "completed", "failed", "rejected", "cancelled"].includes(run.status.toLowerCase()));
  const message = streamPhase === "reconnecting" ? "AgentBus event stream reconnecting. Last authoritative state remains visible."
    : streamPhase === "restored" ? "AgentBus event stream restored."
      : active?.status === "waiting_for_approval" ? "Approval required. Execution is paused."
        : active ? `Run ${active.status}.` : "";
  return <div className="sr-only" aria-live="polite" aria-atomic="true">{message}</div>;
}

export function connectionDestination(currentHash: string): string {
  return currentHash && currentHash !== "#" ? currentHash : "#/";
}

function NavLink({ route, href, label, icon }: { route: string; href: string; label: string; icon: ReactNode }) {
  const target = href.slice(1);
  const active = target === "/" ? route === "/" : route.startsWith(target);
  return <a className={active ? "rail-link is-active" : "rail-link"} href={href} aria-label={label} title={label}>{icon}<span>{label}</span></a>;
}

function useHashRoute(): RouteState {
  const read = (): RouteState => {
    const raw = window.location.hash.slice(1) || "/";
    const [path, query = ""] = raw.split("?", 2);
    return { path: path.startsWith("/") ? path : `/${path}`, query: new URLSearchParams(query) };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

function streamLabel(phase: string): string {
  if (phase === "connected") return "Connected";
  if (phase === "restored") return "Restored";
  if (phase === "reconnecting") return "Reconnecting";
  if (phase === "degraded") return "Degraded";
  return "Snapshot";
}
