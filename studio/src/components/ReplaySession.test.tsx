import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { StudioClient } from "../api/client";
import type { ReplaySessionResponse } from "../api/types";
import { ReplaySession } from "./ReplaySession";

afterEach(() => vi.useRealTimers());

it("keeps slow replay pending beyond three seconds and reports actual failure", async () => {
  vi.useFakeTimers();
  const initial = { replay_id: "replay-slow", status: "running" } as ReplaySessionResponse;
  const replay = vi.fn().mockResolvedValue(initial);
  const client = { replay } as unknown as StudioClient;
  const view = render(<ReplaySession client={client} initial={initial} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(screen.getByRole("status")).toHaveTextContent("Replay running");
  expect(replay.mock.calls.length).toBeGreaterThan(4);
  replay.mockResolvedValue({ ...initial, status: "failed", failure_message: "Captured input missing" });
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(screen.getByRole("status")).toHaveTextContent("Replay failed");
  expect(screen.getByRole("alert")).toHaveTextContent("Captured input missing");
  const count = replay.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
  expect(replay).toHaveBeenCalledTimes(count);
  view.unmount();
});

it("bounds failed status reads and aborts on unmount without declaring success", async () => {
  vi.useFakeTimers();
  const initial = { replay_id: "replay-stale", status: "queued" } as ReplaySessionResponse;
  const replay = vi.fn().mockRejectedValue(new Error("Disconnected"));
  const view = render(<ReplaySession client={{ replay } as unknown as StudioClient} initial={initial} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(replay).toHaveBeenCalledTimes(5);
  expect(screen.getByRole("status")).toHaveTextContent("Stale snapshot: queued");
  view.unmount();
  expect(replay.mock.calls[0][1].signal.aborted).toBe(true);
});
