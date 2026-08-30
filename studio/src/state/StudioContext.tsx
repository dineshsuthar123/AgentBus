import { createContext, useContext, useEffect, useEffectEvent, useRef, useState, type ReactNode } from "react";
import type { DoctorResponse, InfoResponse, ProviderSummary, RunSummary } from "../api/types";
import { StudioClient } from "../api/client";
import { StudioEventStream } from "../api/sse";

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
  eventRevision: number;
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
  const [eventRevision, setEventRevision] = useState(0);
  const refreshTimer = useRef<number | undefined>(undefined);

  async function load(candidate: StudioClient): Promise<void> {
    const [nextInfo, nextProviders, nextDoctor, nextRuns] = await Promise.all([
      candidate.info(),
      candidate.providers(),
      candidate.doctor(),
      candidate.runs()
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
      setConnectionMessage(error instanceof Error ? error.message : "AgentBus Studio could not connect.");
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
  }

  async function refresh(): Promise<void> {
    if (!client) return;
    try {
      await load(client);
      setConnection("connected");
      setConnectionMessage(undefined);
    } catch (error) {
      setConnection("error");
      setConnectionMessage(error instanceof Error ? error.message : "Runtime refresh failed.");
    }
  }

  const refreshFromEvent = useEffectEvent(() => {
    void refresh();
  });

  useEffect(() => {
    if (!client) return;
    const stream = new StudioEventStream(client, {
      onState: setStreamConnected,
      onEvent: () => {
        setEventRevision((value) => value + 1);
        window.clearTimeout(refreshTimer.current);
        refreshTimer.current = window.setTimeout(refreshFromEvent, 180);
      }
    });
    stream.start();
    return () => {
      window.clearTimeout(refreshTimer.current);
      stream.stop();
    };
  }, [client]);

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
      eventRevision,
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
