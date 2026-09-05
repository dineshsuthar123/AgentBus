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

  async function load(candidate: StudioClient, force = false): Promise<void> {
    const options = { force };
    const [nextInfo, nextProviders, nextDoctor, nextRuns] = await Promise.all([
      candidate.info(options),
      candidate.providers(options),
      candidate.doctor(undefined, options),
      candidate.runs(100, options)
    ]);
    setInfo(nextInfo);
    setProviders(nextProviders.providers);
    setDoctor(nextDoctor);
    setRuns(nextRuns.runs);
  }

  async function connect(token: string): Promise<void> {
    setConnection("connecting");
    setConnectionMessage(undefined);
    try {
      const candidate = new StudioClient(token.trim());
      await load(candidate);
      setClient(candidate);
      setConnection("connected");
    } catch (error) {
      setConnection("error");
      setConnectionMessage(error instanceof Error ? error.message : "Syndra Studio could not connect.");
      throw error;
    }
  }

  function disconnect(): void {
    setClient(undefined);
    setConnection("disconnected");
    setConnectionMessage(undefined);
    setInfo(undefined);
    setProviders([]);
    setDoctor(undefined);
    setRuns([]);
    setStreamConnected(false);
    setStreamStatus({ connected: false, cursor: 0, phase: "stopped", reconnectCount: 0 });
    eventStore.clear();
  }

  async function refresh(): Promise<void> {
    if (!client) return;
    setRefreshing(true);
    try {
      await load(client, true);
      setConnection("connected");
      setConnectionMessage(undefined);
    } catch (error) {
      setConnection("error");
      setConnectionMessage(error instanceof Error ? error.message : "Runtime refresh failed.");
    } finally {
      setRefreshing(false);
    }
  }

  const refreshFromEvent = useEffectEvent(async () => {
    if (!client) return;
    try {
      const nextRuns = await client.runs(100, { force: true });
      setRuns(nextRuns.runs);
      setEventRevision((value) => value + 1);
    } catch (error) {
      setConnectionMessage(error instanceof Error ? error.message : "Run reconciliation failed.");
    }
  });

  const reconcileFromStream = useEffectEvent(async () => {
    await refresh();
  });

  useEffect(() => {
    if (!client) return;
    const stream = new StudioEventStream(client, {
      onState: setStreamConnected,
      onStatus: setStreamStatus,
      onDrop: (reason) => eventStore.recordDrop(reason),
      onReconcile: reconcileFromStream,
      onEvent: (event) => {
        eventStore.ingest(event);
        window.clearTimeout(refreshTimer.current);
        refreshTimer.current = window.setTimeout(() => void refreshFromEvent(), 240);
      }
    });
    stream.start();
    return () => {
      window.clearTimeout(refreshTimer.current);
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
