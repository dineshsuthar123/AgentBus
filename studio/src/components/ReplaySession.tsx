import { useEffect, useState } from "react";
import type { StudioClient } from "../api/client";
import type { ReplaySessionResponse } from "../api/types";

const TERMINAL = new Set(["succeeded", "completed", "failed", "cancelled", "rejected", "interrupted"]);

export function ReplaySession({ client, initial }: { client: StudioClient; initial: ReplaySessionResponse }) {
  const [session, setSession] = useState(initial);
  const [error, setError] = useState<string>();
  const [retry, setRetry] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function poll() {
      try {
        const next = await client.replay(initial.replay_id, { force: true, signal: controller.signal });
        if (controller.signal.aborted) return;
        setSession(next);
        setError(undefined);
        failures = 0;
        if (TERMINAL.has(next.status)) return;
      } catch (reason) {
        if (controller.signal.aborted) return;
        failures += 1;
        setError(reason instanceof Error ? reason.message : "Replay status unavailable");
        if (failures >= 5) return;
      }
      timer = setTimeout(() => void poll(), Math.min(10_000, 1_000 * 2 ** failures));
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [client, initial.replay_id, retry]);

  async function cancel() {
    setCancelling(true);
    try {
      await client.cancelReplay(initial.replay_id);
      setRetry((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Replay cancellation failed");
    } finally { setCancelling(false); }
  }

  return <section className="replay-session" aria-label="Replay session">
    <h3>Offline replay</h3>
    <code>{session.replay_id}</code>
    <p role="status">{error ? `Stale snapshot: ${session.status}` : `Replay ${session.status}`}</p>
    {session.failure_message && <p role="alert">{session.failure_message}</p>}
    <p>Provider calls: {session.provider_calls ?? 0}. Network calls: {session.network_calls ?? 0}.
      Process dispatches: {session.process_dispatches ?? 0}. Captured results reused: {session.captured_tool_results_reused ?? 0}.</p>
    {error && <p role="alert">{error} <button onClick={() => setRetry((value) => value + 1)}>Retry status</button></p>}
    {!TERMINAL.has(session.status) && <button disabled={cancelling} onClick={() => void cancel()}>{cancelling ? "Requesting cancellation" : "Cancel replay"}</button>}
  </section>;
}
