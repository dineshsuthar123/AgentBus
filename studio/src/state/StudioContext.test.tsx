import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StudioClient } from "../api/client";
import { StudioProvider, useStudio } from "./StudioContext";

vi.mock("../api/client", () => ({ StudioClient: vi.fn() }));
const { callbacks } = vi.hoisted(() => ({ callbacks: [] as Array<{ onAuthenticationFailure: () => void }> }));
vi.mock("../api/sse", () => ({ StudioEventStream: class {
  constructor(_client: unknown, options: { onAuthenticationFailure: () => void }) { callbacks.push(options); }
  start() {}
  stop() {}
} }));

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

function candidate(id: string) {
  return {
    info: vi.fn(async () => ({ daemon_id: id })),
    providers: vi.fn(async () => ({ providers: [] })),
    doctor: vi.fn(async () => ({ checks: [] })),
    runs: vi.fn(async () => ({ runs: [], total: 0 })),
    dispose: vi.fn(),
    logout: vi.fn(async () => undefined),
  };
}

function Probe() {
  const studio = useStudio();
  return <>
    <output aria-label="Connection">{studio.connection}</output>
    <output aria-label="Daemon">{studio.info?.daemon_id ?? "none"}</output>
    <output aria-label="Run count">{studio.runs.length}</output>
    <button onClick={() => void studio.connect("x".repeat(32)).catch(() => undefined)}>Connect</button>
    <button onClick={studio.disconnect}>Disconnect</button>
    <button onClick={() => void studio.refresh()}>Refresh</button>
  </>;
}

describe("Studio session isolation", () => {
  beforeEach(() => { vi.mocked(StudioClient).mockReset(); callbacks.length = 0; });

  it("ignores a retired stream's auth failure but retires the current expired session", async () => {
    const old = candidate("old");
    const latest = candidate("latest");
    vi.mocked(StudioClient)
      .mockImplementationOnce(function () { return old as unknown as StudioClient; })
      .mockImplementationOnce(function () { return latest as unknown as StudioClient; });
    const user = userEvent.setup();
    render(<StudioProvider><Probe /></StudioProvider>);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(callbacks).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(callbacks).toHaveLength(2));
    act(() => callbacks[0].onAuthenticationFailure());
    expect(screen.getByLabelText("Daemon")).toHaveTextContent("latest");
    act(() => callbacks[1].onAuthenticationFailure());
    expect(screen.getByLabelText("Connection")).toHaveTextContent("error");
    expect(screen.getByLabelText("Daemon")).toHaveTextContent("none");
    expect(latest.dispose).toHaveBeenCalled();
  });

  it("cannot reconnect itself when a disconnected handshake resolves late", async () => {
    const pending = deferred<{ daemon_id: string }>();
    const client = candidate("old");
    client.info.mockImplementation(() => pending.promise);
    vi.mocked(StudioClient).mockImplementation(function () { return client as unknown as StudioClient; });
    const user = userEvent.setup();
    render(<StudioProvider><Probe /></StudioProvider>);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    await act(async () => pending.resolve({ daemon_id: "old" }));

    expect(screen.getByLabelText("Connection")).toHaveTextContent("disconnected");
    expect(screen.getByLabelText("Daemon")).toHaveTextContent("none");
    expect(client.dispose).toHaveBeenCalled();
  });

  it("keeps the latest connection when an older handshake completes last", async () => {
    const pending = deferred<{ daemon_id: string }>();
    const old = candidate("old");
    const latest = candidate("latest");
    old.info.mockImplementation(() => pending.promise);
    vi.mocked(StudioClient)
      .mockImplementationOnce(function () { return old as unknown as StudioClient; })
      .mockImplementationOnce(function () { return latest as unknown as StudioClient; });
    const user = userEvent.setup();
    render(<StudioProvider><Probe /></StudioProvider>);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(screen.getByLabelText("Daemon")).toHaveTextContent("latest"));
    await act(async () => pending.resolve({ daemon_id: "old" }));

    expect(screen.getByLabelText("Daemon")).toHaveTextContent("latest");
    expect(old.dispose).toHaveBeenCalled();
  });

  it("does not repopulate disconnected state from an outstanding refresh", async () => {
    const client = candidate("runtime");
    vi.mocked(StudioClient).mockImplementation(function () { return client as unknown as StudioClient; });
    const user = userEvent.setup();
    render(<StudioProvider><Probe /></StudioProvider>);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(screen.getByLabelText("Connection")).toHaveTextContent("connected"));
    const pending = deferred<{ daemon_id: string }>();
    client.info.mockImplementation(() => pending.promise);
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    await act(async () => pending.resolve({ daemon_id: "runtime" }));

    expect(screen.getByLabelText("Connection")).toHaveTextContent("disconnected");
    expect(screen.getByLabelText("Daemon")).toHaveTextContent("none");
    expect(screen.getByLabelText("Run count")).toHaveTextContent("0");
  });

  it("retires a pending connection when its provider unmounts", async () => {
    const pending = deferred<{ daemon_id: string }>();
    const client = candidate("old");
    client.info.mockImplementation(() => pending.promise);
    vi.mocked(StudioClient).mockImplementation(function () { return client as unknown as StudioClient; });
    const user = userEvent.setup();
    const { unmount } = render(<StudioProvider><Probe /></StudioProvider>);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    unmount();
    await act(async () => pending.resolve({ daemon_id: "old" }));

    expect(client.dispose).toHaveBeenCalled();
  });
});
