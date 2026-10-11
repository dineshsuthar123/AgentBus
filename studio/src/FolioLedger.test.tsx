import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { StudioClient } from "./api/client";
import type { RunSummary } from "./api/types";
import { StudioEventStore } from "./events/eventStore";
import { useStudio } from "./state/StudioContext";

vi.mock("./state/StudioContext", () => ({ useStudio: vi.fn() }));
vi.mock("./components/WorkspaceDrawer", () => ({ WorkspaceDrawer: () => <p>Workspace facilities</p> }));
vi.mock("./components/CommandPalette", () => ({ CommandPalette: () => <button>Commands</button> }));
vi.mock("./components/DiagnosticsOverlay", () => ({ DiagnosticsOverlay: () => null }));
vi.mock("./pages/NewRunPage", () => ({ NewRunPage: () => <p>Task draft</p> }));
vi.mock("./pages/RunPage", () => ({ RunPage: ({ runId }: { runId: string }) => <h1>Inspected {runId}</h1> }));

describe("Ledger continuity", () => {
  it("keeps loaded cursor pages and reading position while a run is inspected, without refetching the first page", async () => {
    localStorage.clear(); window.location.hash = "#/history";
    const records: RunSummary[] = Array.from({ length: 121 }, (_, index) => ({
      run_id: `ledger-${index}`, original_task: `Ledger fixture ${index}`, workspace: "/work/ledger",
      status: "failed", workflow: "multi", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:01:00Z", changed_files: [], version: 1
    }));
    const history = vi.fn(async (_query: string, _status: string, cursor?: string) => {
      const start = cursor === "second" ? 50 : cursor === "third" ? 100 : 0;
      return { runs: records.slice(start, start + 50), total: Math.min(50, records.length - start), next_cursor: start === 0 ? "second" : start === 50 ? "third" : null };
    });
    vi.mocked(useStudio).mockReturnValue({
      client: { history, run: vi.fn(async (id: string) => records.find((run) => run.run_id === id)) } as unknown as StudioClient,
      connection: "connected", providers: [], runs: records.slice(0, 100), streamConnected: true,
      streamStatus: { connected: true, cursor: 0, phase: "connected", reconnectCount: 0 },
      eventStore: new StudioEventStore(), eventRevision: 0, refreshing: false, connect: vi.fn(), disconnect: vi.fn(), refresh: vi.fn()
    });
    render(<App />);
    const user = userEvent.setup();
    const ledger = (await screen.findByRole("heading", { name: "All runs" })).closest(".history-page") as HTMLElement;
    await user.click(await within(ledger).findByRole("button", { name: "Load more runs" }));
    await within(ledger).findByText("Ledger fixture 99");
    await user.click(within(ledger).getByRole("button", { name: "Load more runs" }));
    await within(ledger).findByText("End of history");
    expect(within(ledger).getAllByRole("link")).toHaveLength(121);
    const paper = screen.getByRole("main");
    paper.scrollTop = 750; fireEvent.scroll(paper);
    await user.click(within(ledger).getByRole("link", { name: /Ledger fixture 120/ }));
    await screen.findByRole("heading", { name: "Inspected ledger-120" });
    expect(ledger.closest("[hidden]")).not.toBeNull();
    await user.click(screen.getByRole("link", { name: "All runs" }));
    await screen.findByRole("heading", { name: "All runs" });
    expect(within(ledger).getAllByRole("link")).toHaveLength(121);
    expect(paper.scrollTop).toBe(750);
    expect(history).toHaveBeenCalledTimes(3);
  });
});
