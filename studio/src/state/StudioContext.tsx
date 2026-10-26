import { createContext, useContext, useEffect, useEffectEvent, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import type { DoctorResponse, InfoResponse, ProviderSummary, RunSummary } from "../api/types";
import { StudioClient } from "../api/client";
import { StudioEventStream, type StreamStatus } from "../api/sse";
import { StudioEventStore } from "../events/eventStore";

type ConnectionStatus = "disconnected" | "connecting" | "connected" | "error";

interface StudioContextValue {
  client?: StudioClient;
  connection: ConnectionStatus;
  connectionMessage?: string;
  info?: InfoResponse;
  providers: ProviderSummary[];
  doctor?: DoctorResponse;
  runs: RunSummary[];
  streamConnected: boolean;
  streamStatus: StreamStatus;
  eventStore: StudioEventStore;
  eventRevision: number;
  refreshing: boolean;
  connect: (token: string) => Promise<void>;
  disconnect: () => void;
  refresh: () => Promise<void>;
}

const StudioContext = createContext<StudioContextValue | undefined>(undefined);

export function StudioProvider({ children }: { children: ReactNode }) {
  const [client, setClient] = useState<StudioClient>();
  const [connection, setConnection] = useState<ConnectionStatus>("disconnected");
  const [connectionMessage, setConnectionMessage] = useState<string>();
  const [info, setInfo] = useState<InfoResponse>();
  const [providers, setProviders] = useState<ProviderSummary[]>([]);
  const [doctor, setDoctor] = useState<DoctorResponse>();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [streamConnected, setStreamConnected] = useState(false);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>({
    connected: false,
    cursor: 0,
    phase: "stopped",
    reconnectCount: 0
  });
  const [eventRevision, setEventRevision] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [eventStore] = useState(() => new StudioEventStore());
  const refreshTimer = useRef<number | undefined>(undefined);
  const sessionGeneration = useRef(0);
  const activeClient = useRef<StudioClient | undefined>(undefined);
  const sessionController = useRef<AbortController | undefined>(undefined);

  async function load(candidate: StudioClient, force = false, signal?: AbortSignal) {
    const options = { force, signal };
    const [nextInfo, nextProviders, nextDoctor, nextRuns] = await Promise.all([
      candidate.info(options),
      candidate.providers(options),
      candidate.doctor(undefined, options),
      candidate.runs(100, options)
    ]);
    return { info: nextInfo, providers: nextProviders.providers, doctor: nextDoctor, runs: nextRuns.runs };
  }

  function commitSnapshot(snapshot: Awaited<ReturnType<typeof load>>): void {
    setInfo(snapshot.info);
    setProviders(snapshot.providers);
    setDoctor(snapshot.doctor);
    setRuns(snapshot.runs);
  }

  function retireSession(): StudioClient | undefined {
    sessionGeneration.current += 1;
    sessionController.current?.abort();
    const previous = activeClient.current;
    activeClient.current = undefined;
    previous?.dispose();
    return previous;
  }

  function clearSessionState(): void {
    setClient(undefined);
    setInfo(undefined);
    setProviders([]);
    setDoctor(undefined);
    setRuns([]);
    setRefreshing(false);
    setStreamConnected(false);
    setStreamStatus({ connected: false, cursor: 0, phase: "stopped", reconnectCount: 0 });
    setEventRevision(0);
    eventStore.clear();
  }

  async function connect(token: string): Promise<void> {
    retireSession();
    clearSessionState();
    const generation = sessionGeneration.current;
    setConnection("connecting");
    setConnectionMessage(undefined);
    try {
      const candidate = new StudioClient(token.trim());
      const controller = new AbortController();
      activeClient.current = candidate;
      sessionController.current = controller;
      const snapshot = await load(candidate, false, controller.signal);
      if (generation !== sessionGeneration.current) throw new DOMException("Studio connection was cancelled.", "AbortError");
      commitSnapshot(snapshot);
      setClient(candidate);
      setConnection("connected");
    } catch (error) {
      if (generation !== sessionGeneration.current) throw error;
      retireSession();
      setConnection("error");
      setConnectionMessage(error instanceof Error ? error.message : "Syndra Studio could not connect.");
      throw error;
    }
  }

  const connectBrowserSession = useEffectEvent(() => {
    void connect("").catch(() => undefined);
  });
  useEffect(() => {
    if (!window.location.pathname.startsWith("/studio")) return;
    const timer = window.setTimeout(connectBrowserSession, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => () => {
    sessionGeneration.current += 1;
    sessionController.current?.abort();
    activeClient.current?.dispose();
    activeClient.current = undefined;
  }, []);

  function disconnect(): void {
    const previous = retireSession();
    const generation = sessionGeneration.current;
    clearSessionState();
    setConnection("disconnected");
    setConnectionMessage(undefined);
    void previous?.logout().catch(() => {
      if (generation === sessionGeneration.current) setConnectionMessage("Disconnected in this browser. The runtime did not confirm session revocation; close this window and relaunch Studio to establish a new session.");
    });
  }

  async function refresh(): Promise<void> {
    if (!client || activeClient.current !== client) return;
    const generation = sessionGeneration.current;
    setRefreshing(true);
    try {
      const snapshot = await load(client, true, sessionController.current?.signal);
      if (generation !== sessionGeneration.current) return;
      commitSnapshot(snapshot);
      setConnection("connected");
      setConnectionMessage(undefined);
    } catch (error) {
      if (generation !== sessionGeneration.current) return;
      setConnection("error");
      setConnectionMessage(error instanceof Error ? error.message : "Runtime refresh failed.");
    } finally {
      if (generation === sessionGeneration.current) setRefreshing(false);
    }
  }

  const refreshFromEvent = useEffectEvent(async () => {
    if (!client || activeClient.current !== client) return;
    const generation = sessionGeneration.current;
    try {
      const nextRuns = await client.runs(100, { force: true, signal: sessionController.current?.signal });
      if (generation !== sessionGeneration.current) return;
      setRuns(nextRuns.runs);
      setEventRevision((value) => value + 1);
    } catch (error) {
      if (generation !== sessionGeneration.current) return;
      setConnectionMessage(error instanceof Error ? error.message : "Run reconciliation failed.");
    }
  });

  const reconcileFromStream = useEffectEvent(async () => {
    await refresh();
  });

  const authenticationFailed = useEffectEvent((source: StudioClient) => {
    if (activeClient.current !== source) return;
    retireSession();
    clearSessionState();
    setConnection("error");
    setConnectionMessage("Studio session expired or was revoked. Launch syndra studio again, or reconnect from the extension.");
  });

  useEffect(() => {
    if (!client) return;
    const stream = new StudioEventStream(client, {
      onState: (value) => { if (activeClient.current === client) setStreamConnected(value); },
      onStatus: (value) => { if (activeClient.current === client) setStreamStatus(value); },
      onDrop: (reason) => { if (activeClient.current === client) eventStore.recordDrop(reason); },
      onReconcile: reconcileFromStream,
      onAuthenticationFailure: () => authenticationFailed(client),
      onEvent: (event) => {
        if (activeClient.current !== client) return;
        eventStore.ingest(event);
        if (refreshTimer.current === undefined) {
          refreshTimer.current = window.setTimeout(() => {
            refreshTimer.current = undefined;
            void refreshFromEvent();
          }, 240);
        }
      }
    });
    stream.start();
    return () => {
      window.clearTimeout(refreshTimer.current);
      refreshTimer.current = undefined;
      stream.stop();
    };
  }, [client, eventStore]);

  return (
    <StudioContext.Provider value={{
      client,
      connection,
      connectionMessage,
      info,
      providers,
      doctor,
      runs,
      streamConnected,
      streamStatus,
      eventStore,
      eventRevision,
      refreshing,
      connect,
      disconnect,
      refresh
    }}>
      {children}
    </StudioContext.Provider>
  );
}

export function useStudio(): StudioContextValue {
  const value = useContext(StudioContext);
  if (!value) throw new Error("useStudio must be used inside StudioProvider");
  return value;
}

export function useRunEvents(runId: string) {
  const { eventStore } = useStudio();
  return useSyncExternalStore(
    (listener) => eventStore.subscribeRun(runId, listener),
    () => eventStore.getRunSnapshot(runId),
    () => eventStore.getRunSnapshot(runId)
  );
}

export function useEventDiagnostics() {
  const { eventStore } = useStudio();
  return useSyncExternalStore(
    eventStore.subscribe,
    eventStore.getSnapshot,
    eventStore.getSnapshot
  );
}
