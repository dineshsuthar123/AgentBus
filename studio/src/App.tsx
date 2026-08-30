import {
  Activity,
  Braces,
  Command,
  Gauge,
  History,
  LogOut,
  Play,
  Plus,
  Radio,
  ShieldCheck,
  Sparkles
} from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { DashboardPage } from "./pages/DashboardPage";
import { DemoPage } from "./pages/DemoPage";
import { HistoryPage } from "./pages/HistoryPage";
import { NewRunPage } from "./pages/NewRunPage";
import { RunPage } from "./pages/RunPage";
import { RuntimePage } from "./pages/RuntimePage";
import { useStudio } from "./state/StudioContext";

interface RouteState {
  path: string;
  query: URLSearchParams;
}

export function App() {
  const studio = useStudio();
  const route = useHashRoute();

  if (!studio.client) {
    return <ConnectionScreen />;
  }

  const runMatch = /^\/runs\/([^/]+)$/.exec(route.path);
  let page: ReactNode;
  if (runMatch) page = <RunPage runId={decodeURIComponent(runMatch[1])} />;
  else if (route.path === "/new") page = <NewRunPage demo={route.query.get("demo")} />;
  else if (route.path === "/history") page = <HistoryPage />;
  else if (route.path === "/runtime") page = <RuntimePage />;
  else if (route.path === "/demo") page = <DemoPage />;
  else page = <DashboardPage />;

  return (
    <div className="studio-shell">
      <aside className="studio-nav">
        <a className="brand-lockup" href="#/" aria-label="AgentBus Studio home">
          <img src="/agentbus-mark.svg" alt="" />
          <span><strong>AgentBus</strong><small>Studio</small></span>
        </a>
        <nav aria-label="Studio navigation">
          <NavLink route={route.path} href="#/" label="Command" icon={<Command size={17} />} />
          <NavLink route={route.path} href="#/new" label="New run" icon={<Plus size={17} />} />
          <NavLink route={route.path} href="#/history" label="History" icon={<History size={17} />} />
          <NavLink route={route.path} href="#/runtime" label="Runtime" icon={<Gauge size={17} />} />
        </nav>
        <div className="nav-divider" />
        <a className={route.path === "/demo" ? "nav-link is-active" : "nav-link"} href="#/demo">
          <Sparkles size={17} /><span>Payment demo</span>
        </a>
        <div className="nav-foot">
          <div className={`stream-pill ${studio.streamConnected ? "is-live" : ""}`}>
            <Radio size={13} /><span>{studio.streamConnected ? "Event stream live" : "Snapshot mode"}</span>
          </div>
          <button className="nav-link nav-button" type="button" onClick={studio.disconnect}>
            <LogOut size={16} /><span>Disconnect</span>
          </button>
        </div>
      </aside>
      <main className="studio-main">{page}</main>
    </div>
  );
}

function ConnectionScreen() {
  const { connect, connection, connectionMessage } = useStudio();
  const [token, setToken] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await connect(token);
      window.location.hash = "#/";
    } catch {
      // The context exposes the sanitized connection error.
    }
  }

  return (
    <main className="connection-screen">
      <section className="connection-story">
        <div className="brand-lockup brand-lockup-large"><img src="/agentbus-mark.svg" alt="" /><span><strong>AgentBus</strong><small>Studio</small></span></div>
        <p className="connection-kicker"><ShieldCheck size={15} /> Local execution control plane</p>
        <h1>See the agent.<br /><span>Trust the evidence.</span></h1>
        <p className="connection-lede">A flight deck for durable coding runs, exact approval gates, bounded repository changes, and providerless replay.</p>
        <div className="connection-rail" aria-hidden="true">
          <span><Braces size={16} /></span><i /><span><Activity size={16} /></span><i /><span><ShieldCheck size={16} /></span>
        </div>
      </section>
      <section className="connection-panel">
        <div>
          <p className="eyebrow">Loopback session</p>
          <h2>Connect to AgentBus</h2>
          <p>Start the authenticated local daemon, then enter its one-time bearer token. The token stays in browser memory.</p>
        </div>
        <pre className="startup-command"><code>agentbus serve --port 8765 --json-ready</code></pre>
        <form onSubmit={(event) => void submit(event)}>
          <label><span>Session token</span><input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Paste bearer_token from the ready handshake" minLength={32} required /></label>
          {connectionMessage && <p className="inline-error" role="alert">{connectionMessage}</p>}
          <button className="button button-primary button-wide" disabled={connection === "connecting"} type="submit">
            {connection === "connecting" ? <Activity className="spin" size={16} /> : <Play size={16} />}
            {connection === "connecting" ? "Establishing control path" : "Open Studio"}
          </button>
        </form>
        <div className="security-note"><span>01</span><p><strong>Same-origin by design.</strong> Vite proxies only to the configured numeric-loopback daemon; AgentBus keeps its no-CORS security boundary.</p></div>
      </section>
    </main>
  );
}

function NavLink({ route, href, label, icon }: { route: string; href: string; label: string; icon: ReactNode }) {
  const target = href.slice(1);
  const active = target === "/" ? route === "/" : route.startsWith(target);
  return <a className={active ? "nav-link is-active" : "nav-link"} href={href}>{icon}<span>{label}</span></a>;
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
