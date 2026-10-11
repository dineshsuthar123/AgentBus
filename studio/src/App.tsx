import {
  Activity,
  Play
} from "lucide-react";
import { lazy, Suspense, useEffect, useState, type FormEvent, type ReactNode } from "react";
import type { RunSummary } from "./api/types";
import { FolioShell } from "./components/FolioShell";
import { WorkspaceDrawer } from "./components/WorkspaceDrawer";
import { LoadingState } from "./components/Primitives";
import { WorkshopJourney } from "./components/WorkshopJourney";
import { decodeHashSegment } from "./lib/routing";
import { useStudio } from "./state/StudioContext";
import { readResumeFolio } from "./state/folioNavigation";

const DemoPage = lazy(() => import("./pages/DemoPage").then((module) => ({ default: module.DemoPage })));
const HistoryPage = lazy(() => import("./pages/HistoryPage").then((module) => ({ default: module.HistoryPage })));
const NewRunPage = lazy(() => import("./pages/NewRunPage").then((module) => ({ default: module.NewRunPage })));
const RunPage = lazy(() => import("./pages/RunPage").then((module) => ({ default: module.RunPage })));
const RuntimePage = lazy(() => import("./pages/RuntimePage").then((module) => ({ default: module.RuntimePage })));
const WorkshopPage = lazy(() => import("./pages/WorkshopPage").then((module) => ({ default: module.WorkshopPage })));
const DiagnosticsOverlay = lazy(() => import("./components/DiagnosticsOverlay").then((module) => ({ default: module.DiagnosticsOverlay })));

interface RouteState {
  path: string;
  query: URLSearchParams;
}

export function App() {
  const studio = useStudio();
  const route = useHashRoute();
  const runMatch = /^\/runs\/([^/]+)$/.exec(route.path);
  const runId = runMatch ? decodeHashSegment(runMatch[1]) : undefined;
  const ledgerVisible = !runId && !["/new", "/runtime", "/demo", "/workshop"].includes(route.path);
  const [ledgerOpened, setLedgerOpened] = useState(ledgerVisible);
  if (ledgerVisible && !ledgerOpened) setLedgerOpened(true);
  const [openedRun, setOpenedRun] = useState<RunSummary>();
  const recentRun = studio.runs.find((run) => run.run_id === runId);
  useEffect(() => {
    if (!runId || recentRun || !studio.client) return;
    const controller = new AbortController();
    void studio.client.run(runId, { signal: controller.signal }).then((run) => { if (!controller.signal.aborted) setOpenedRun(run); }).catch(() => { /* The run surface displays authoritative errors. */ });
    return () => controller.abort();
  }, [runId, recentRun, studio.client]);
  useEffect(() => {
    if (route.path !== "/" || !studio.client) return;
    const candidate = readResumeFolio();
    if (!candidate) return;
    const controller = new AbortController();
    const startHash = window.location.hash;
    void studio.client.run(candidate.id, { signal: controller.signal }).then((run) => {
      if (controller.signal.aborted || window.location.hash !== startHash || run.run_id !== candidate.id) return;
      setOpenedRun(run);
      window.history.replaceState(null, "", candidate.href);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }).catch(() => { /* An unavailable selection falls back to the persisted ledger. */ });
    return () => controller.abort();
  }, [route.path, studio.client]);
  if (!studio.client) return <ConnectionScreen />;
  const currentRun = recentRun ?? (openedRun?.run_id === runId ? openedRun : undefined);
  let page: ReactNode;
  if (runId) page = <RunPage key={runId} runId={runId} query={route.query} />;
  else if (route.path === "/new") page = null;
  else if (route.path === "/history") page = null;
  else if (route.path === "/runtime") page = <RuntimePage />;
  else if (route.path === "/demo") page = <DemoPage />;
  else if (route.path === "/workshop") page = <WorkshopPage />;
  else page = null;

  return (
    <FolioShell runs={currentRun && !recentRun ? [...studio.runs, currentRun] : studio.runs} selected={runId} phase={studio.streamStatus.phase} onDisconnect={studio.disconnect} drawer={runId && !currentRun ? <p role="status">Selected run workspace is unavailable. Wait for its scoped record or inspect the run's error. No repository action is enabled.</p> : <WorkspaceDrawer query={route.query} run={currentRun} />}>
        <Suspense fallback={<LoadingState label="Opening Studio surface" />}>{page}</Suspense>
        {ledgerOpened && <div hidden={!ledgerVisible}><Suspense fallback={<LoadingState label="Opening persisted ledger" />}><HistoryPage /></Suspense></div>}
        <div hidden={route.path !== "/new"}><Suspense fallback={<LoadingState label="Opening task draft" />}><NewRunPage demo={route.query.get("demo")} focus={route.query.get("focus")} /></Suspense></div>
      <StudioAnnouncements runs={studio.runs} streamPhase={studio.streamStatus.phase} />
      {import.meta.env.DEV && <Suspense><DiagnosticsOverlay /></Suspense>}
    </FolioShell>
  );
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
    <main className="connection-workshop">
      <WorkshopJourney entry={<section className="connection-panel" aria-label="Connect to Syndra">
        <div><p className="eyebrow">Loopback session</p><h2>Connect to Syndra</h2><p>Run Syndra Studio locally to open a browser session automatically. If this session expired, use Open Studio from the connected extension, or explicitly stop your owned daemon before relaunching.</p></div>
        <pre className="startup-command"><code>syndra studio --workspace .</code></pre>
        {connectionMessage && <p className="inline-error" role="alert">{connectionMessage}</p>}
        <details><summary>Developer recovery: bearer connection</summary><p>For a contributor's same-origin Vite proxy or a known local daemon only. Bearer credentials stay in memory, never in the URL or browser storage.</p><form onSubmit={(event) => void submit(event)}>
          <label><span>Session token</span><input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Paste bearer_token from the ready handshake" minLength={32} required /></label>
          <button className="button button-primary button-wide" disabled={connection === "connecting"} type="submit">{connection === "connecting" ? <Activity className="spin" size={16} /> : <Play size={16} />}{connection === "connecting" ? "Establishing control path" : "Open Studio"}</button>
        </form></details>
        <div className="security-note"><span>01</span><p><strong>Same-origin by design.</strong> Installed Studio uses a short-lived launch exchange and an HTTP-only session. Only numeric-loopback HTTP is supported.</p></div>
      </section>} />
    </main>
  );
}

export function StudioAnnouncements({ runs, streamPhase }: { runs: RunSummary[]; streamPhase: string }) {
  const active = runs.find((run) => !["succeeded", "completed", "failed", "rejected", "cancelled"].includes(run.status.toLowerCase()));
  const latest = runs[0];
  const latestStatus = latest?.status.toLowerCase();
  const message = streamPhase === "reconnecting" ? "Syndra event stream reconnecting. Last authoritative state remains visible."
    : streamPhase === "restored" ? "Syndra event stream restored."
      : active?.status === "waiting_for_approval" ? "Approval required. Execution is paused."
        : active ? `Run ${active.status}.`
          : latestStatus === "succeeded" || latestStatus === "completed" ? "Run succeeded. Durable verification and review state are available."
            : latestStatus === "failed" || latestStatus === "rejected" || latestStatus === "cancelled" ? `Run ${latestStatus}. Inspect the persisted failure evidence.` : "";
  return <div className="sr-only" aria-live="polite" aria-atomic="true">{message}</div>;
}

export function connectionDestination(currentHash: string): string {
  return currentHash && currentHash !== "#" ? currentHash : "#/";
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
