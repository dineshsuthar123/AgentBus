import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RunSummary } from "../api/types";
import { FolioShell } from "./FolioShell";

vi.mock("./CommandPalette", () => ({ CommandPalette: () => <button>Commands</button> }));

const runs: RunSummary[] = ["first", "second"].map((id) => ({
  run_id: id, original_task: `Repair ${id}`, status: "succeeded", workflow: "multi",
  workspace: `/work/${id}`, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:01:00Z",
  changed_files: [], version: 1
}));

function surface(selected = "first") {
  return <FolioShell runs={runs} selected={selected} phase="connected" drawer={<p>Connections</p>} onDisconnect={vi.fn()}><p>Actual artifact</p></FolioShell>;
}

describe("Folio working set", () => {
  beforeEach(() => { localStorage.clear(); window.location.hash = "#/runs/first?view=evidence"; });

  it("returns to the inspected section without attaching the workspace drawer to another run", () => {
    const { rerender } = render(surface());
    window.location.hash = "#/runs/first?view=evidence&drawer=connections";
    rerender(surface());
    window.location.hash = "#/runs/second?view=verifier";
    rerender(surface("second"));
    const shelf = screen.getByRole("navigation", { name: "Open runs" });
    expect(within(shelf).getByRole("link", { name: /Repair first/ })).toHaveAttribute("href", "#/runs/first?view=evidence");
    expect(within(shelf).getByRole("link", { name: /Repair second/ })).toHaveAttribute("href", "#/runs/second?view=verifier");
  });

  it("closing the selected object opens another folio without cancelling or deleting persisted work", async () => {
    const user = userEvent.setup();
    render(surface());
    await user.click(screen.getByRole("button", { name: "Close Repair first from shelf" }));
    expect(window.location.hash).toBe("#/runs/second");
    expect(screen.queryByRole("link", { name: /Repair first/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Repair second/ })).toBeInTheDocument();
  });

  it("keeps the mobile selector and close action tied to the selected persisted run", async () => {
    const user = userEvent.setup();
    render(surface());
    expect(screen.getByRole("combobox", { name: "Open runs" })).toHaveValue("first");
    await user.click(screen.getByRole("button", { name: "Close selected run from working queue" }));
    expect(window.location.hash).toBe("#/runs/second");
    expect(screen.queryByRole("link", { name: /Repair first/ })).not.toBeInTheDocument();
  });

  it("keyboard skip keeps the selected run and section rather than routing to an anchor-shaped page", async () => {
    const user = userEvent.setup();
    render(surface());
    await user.click(screen.getByRole("link", { name: "Skip to work" }));
    expect(screen.getByRole("main")).toHaveFocus();
    expect(window.location.hash).toBe("#/runs/first?view=evidence");
  });

  it("opens a non-modal drawer with focus and returns it to the workspace edge on Escape", async () => {
    const user = userEvent.setup();
    const { rerender } = render(surface());
    await user.click(screen.getByRole("link", { name: "Workspace" }));
    rerender(surface());
    expect(screen.getByRole("heading", { name: "Workspace" })).toHaveFocus();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    rerender(surface());
    expect(screen.getByRole("link", { name: "Workspace" })).toHaveFocus();
    expect(window.location.hash).toBe("#/runs/first?view=evidence");
    expect(screen.queryByRole("heading", { name: "Workspace" })).not.toBeInTheDocument();
  });

  it("does not persist an unvalidated route-shaped identifier from a failed deep link", () => {
    window.location.hash = "#/runs/unvalidated-private-value?view=evidence";
    render(<FolioShell runs={runs} selected="unvalidated-private-value" phase="connected" drawer={null} onDisconnect={vi.fn()}><p>Run unavailable</p></FolioShell>);
    expect(localStorage.getItem("syndra.studio.last-folio")).toBeNull();
  });

  it("retains identified shelf objects when they leave the recent server page", () => {
    const { rerender } = render(surface());
    window.location.hash = "#/runs/second?view=verifier";
    rerender(<FolioShell runs={[runs[1]]} selected="second" phase="connected" drawer={null} onDisconnect={vi.fn()}><p>Checks</p></FolioShell>);
    const first = within(screen.getByRole("navigation", { name: "Open runs" })).getByRole("link", { name: /Repair first/ });
    expect(first).toHaveTextContent("succeeded");
    expect(first).toHaveAttribute("href", "#/runs/first?view=evidence");
  });
});
