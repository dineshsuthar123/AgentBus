import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { StudioClient } from "./api/client";
import type { RunSummary } from "./api/types";
import { StudioEventStore } from "./events/eventStore";
import { saveResumeFolio } from "./state/folioNavigation";
import { useStudio } from "./state/StudioContext";

vi.mock("./state/StudioContext", () => ({ useStudio: vi.fn() }));
vi.mock("./components/WorkspaceDrawer", () => ({ WorkspaceDrawer: ({ run }: { run?: RunSummary }) => <p>Scope: {run?.workspace}</p> }));
vi.mock("./components/CommandPalette", () => ({ CommandPalette: () => <button>Commands</button> }));
vi.mock("./components/DiagnosticsOverlay", () => ({ DiagnosticsOverlay: () => null }));
vi.mock("./pages/HistoryPage", () => ({ HistoryPage: () => <h1>Persisted ledger</h1> }));
vi.mock("./pages/NewRunPage", () => ({ NewRunPage: () => <label>Task draft<textarea /></label> }));
vi.mock("./pages/RunPage", () => ({ RunPage: ({ runId, query }: { runId: string; query: URLSearchParams }) => <h1>Run {runId} / {query.get("view") ?? "run"}</h1> }));

const older: RunSummary = {
  run_id: "older", original_task: "A persisted run beyond the recent page", workspace: "/work/original-scope",
  status: "succeeded", workflow: "multi", created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:01:00Z", changed_files: [], version: 1
};

function connect(run: ReturnType<typeof vi.fn>) {
  vi.mocked(useStudio).mockReturnValue({
    client: { run } as unknown as StudioClient, connection: "connected", providers: [], runs: [],
    streamConnected: true, streamStatus: { connected: true, cursor: 0, phase: "connected", reconnectCount: 0 },
    eventStore: new StudioEventStore(), eventRevision: 0, refreshing: false,
    connect: vi.fn(), disconnect: vi.fn(), refresh: vi.fn()
  });
}

describe("Returning to the work", () => {
  beforeEach(() => { localStorage.clear(); window.location.hash = "#/"; });

  it("validates the last selection through the daemon even when it is older than the recent hundred", async () => {
    const run = vi.fn().mockResolvedValue(older);
    connect(run);
    saveResumeFolio("#/runs/older?view=evidence");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Run older / evidence" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/runs/older?view=evidence");
    expect(run).toHaveBeenCalledWith("older", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    await userEvent.setup().click(screen.getByRole("link", { name: "Workspace" }));
    expect(await screen.findByText("Scope: /work/original-scope")).toBeInTheDocument();
    expect(window.location.hash).toContain("view=evidence");
  });

  it("keeps the ledger available when a saved selection no longer exists", async () => {
    const run = vi.fn().mockRejectedValue(new Error("Run does not exist"));
    connect(run);
    saveResumeFolio("#/runs/missing?view=source");
    render(<App />);
    await waitFor(() => expect(run).toHaveBeenCalled());
    expect(await screen.findByRole("heading", { name: "Persisted ledger" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/");
  });

  it("never overrides an explicit extension deep link with the last selected run", async () => {
    const run = vi.fn().mockResolvedValue({ ...older, run_id: "direct" });
    connect(run);
    saveResumeFolio("#/runs/older?view=evidence");
    window.location.hash = "#/runs/direct?view=verifier";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Run direct / verifier" })).toBeInTheDocument();
    expect(run.mock.calls.every(([id]) => id === "direct")).toBe(true);
    expect(window.location.hash).toBe("#/runs/direct?view=verifier");
  });

  it("does not pull a user out of a new draft when delayed selection validation finishes", async () => {
    let receive!: (run: RunSummary) => void;
    connect(vi.fn(() => new Promise<RunSummary>((resolve) => { receive = resolve; })));
    saveResumeFolio("#/runs/older?view=evidence");
    render(<App />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("link", { name: "New task" }));
    await user.type(await screen.findByLabelText("Task draft"), "Preserve this task");
    await act(async () => receive(older));
    expect(window.location.hash).toBe("#/new");
    expect(screen.getByLabelText("Task draft")).toHaveValue("Preserve this task");
  });

  it("never mounts repository facilities against the launch workspace while an older run's own workspace is unresolved", async () => {
    let receive!: (run: RunSummary) => void;
    connect(vi.fn(() => new Promise<RunSummary>((resolve) => { receive = resolve; })));
    window.location.hash = "#/runs/older?view=source&drawer=repository";
    render(<App />);
    expect(await screen.findByText(/selected run workspace is unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Scope:/)).not.toBeInTheDocument();
    await act(async () => receive(older));
    expect(await screen.findByText("Scope: /work/original-scope")).toBeInTheDocument();
    expect(window.location.hash).toBe("#/runs/older?view=source&drawer=repository");
  });
});
