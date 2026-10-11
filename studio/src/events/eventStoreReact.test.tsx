import { act, render, screen } from "@testing-library/react";
import { useSyncExternalStore } from "react";
import { expect, it } from "vitest";
import { StudioEventStore } from "./eventStore";

it("mounts a cold run before SSE and reconciles its first event and reset without a render loop", () => {
  const store = new StudioEventStore(10, () => undefined);
  let renders = 0;
  function ColdRun() {
    const snapshot = useSyncExternalStore((listener) => store.subscribeRun("cold", listener), () => store.getRunSnapshot("cold"));
    renders += 1;
    return <p>Captured events: {snapshot.events.length}</p>;
  }
  render(<ColdRun />);
  expect(screen.getByText("Captured events: 0")).toBeInTheDocument();
  act(() => {
    store.ingest({ sequence: 1, run_id: "cold", event_type: "run.created", timestamp: "2026-10-05T00:00:00Z", payload: {} });
    store.flush();
  });
  expect(screen.getByText("Captured events: 1")).toBeInTheDocument();
  act(() => store.clear());
  expect(screen.getByText("Captured events: 0")).toBeInTheDocument();
  expect(renders).toBeLessThan(8);
});
